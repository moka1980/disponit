#!/usr/bin/env bash
# ============================================================
# Disponit staging: PostgreSQL-oppsett på Cloud Server S (Ubuntu)
# Kjøres som root av Claude Code. Idempotent.
# ============================================================
set -euo pipefail

DB=disponit
BRUKER=disponit              # RUNTIME — kun DML, eier ingenting
MIGRATOR=disponit_migrator   # eier skjemaet, kjører migrasjoner
AUTH=disponit_authenticator  # eier api_tokener; runtime naar den aldri direkte
TOKENADMIN=disponit_token_admin  # administrerer tokens, eier ingenting
M37=disponit_m37_claimer     # PR-006: eier arbeidskapabiliteter + claim-funksjonene
POLICYEIER=disponit_policy_eier  # PR-013: eier den herdede aktiver_policy-funksjonen
MODULEIER=disponit_modul_eier    # PR-014a: eier modulregisterets overgangsfunksjoner
MODULESADMIN=disponit_modules_admin  # PR-014a: EXECUTE på overgangsfunksjonene
EGRESS=disponit_egress           # PR-014b: egress-proxyens rolle, SELECT kun paa visningen
DOMENEEIER=disponit_domene_eier  # PR-014b: eier domene/artefakt-funksjonene (BYPASSRLS: takeover er kryss-tenant)
DOMAINSADMIN=disponit_domains_admin  # PR-014b: EXECUTE paa domenefunksjonene
ADJUDIKATOR=disponit_domains_adjudicator  # 041: policy-avgrenset SELECT paa overtakelsessaker
# PR-015 (Codex P1): EGEN, minst-privilegert rolle for driftstimerne
# (revalidering + rydding). $DOMAINSADMIN baerer OGSAA direkte EXECUTE paa
# avgjor_domeneovertakelse (016) — en holder av DEN credentialen kunne kalt
# adjudikasjonen med p_godkjent=true og ÉN aktoer, og dermed omgaatt fire
# oeyne helt. Timerne faar derfor sin egen LOGIN-rolle, granted KUN
# revalideringsfunksjonene og rydd_staged_artefakter(int) i migrasjon 019 —
# aldri avgjor_domeneovertakelse, aldri verifiser_domenekontroll.
DOMENER=disponit_domener
MILJOFIL=/etc/disponit/staging.env

# Rolleskillet er Codex' P1 fra PR-004-reviewen: eide runtime-rollen
# tabellene, kunne den skru av eller slette append-only-triggerne selv.
# En vakt du kan fjerne er ingen vakt.

apt-get update -q
apt-get install -y -q postgresql postgresql-contrib
systemctl enable --now postgresql

# Roller + database (idempotent)
# Roller er KLYNGEobjekter og opprettes her, med superbrukeren — aldri i en
# migrasjon. Draften til PR-005 gjorde det siste, og det feiler i vaart
# oppsett fordi migratorrollen verken har eller skal ha CREATEROLE.
# PR-009 v2 §3: arbeideren har EGEN DB-rolle — et kompromittert API har
# ikke arbeiderens fullmakter, og omvendt. Rollen får runtime-grantsettet
# MINUS verifiser_token (API-autentisering er ikke arbeiderens jobb);
# skillet settes i migrer.py.
ARBEIDER=disponit_arbeider
# Varselsenderen har EGEN DB-rolle, av samme grunn som arbeideren: et
# kompromittert web-API skal ikke ha senderens kryss-tenant-vindu, og en
# kompromittert sender skal ikke ha API-ets DML. Rollen får KUN EXECUTE på de
# tre senderfunksjonene (migrer.py) — ingen tabellrettigheter i det hele tatt.
VARSLER=disponit_varselsender
# 048 (#108): plan-arbeideren har EGEN DB-rolle — varselsender-modellen,
# ordrett: et kompromittert web-API skal ikke kunne claime/terminalisere
# planvinduer, og en kompromittert plan-arbeider skal ikke ha API-ets
# fulle DML. Rollen får bestillingsveiens delmengde + claim-funksjonene
# (migrer.py PLAN_RETTIGHETER); runtime MISTER claim-EXECUTE i 048.
PLANARB=disponit_plan_arbeider
# 049/#117 (Codex P1): CI-attesten og aksepten skal bæres av TO
# identiteter. Verifikatoren er innloggingsrollen som SKRIVER attesten,
# og den er MED VILJE ikke medlem av modules_admin: én innlogging skal
# aldri kunne gjøre begge rolleskiftene i m56-aksept.py. Attestant og
# akseptør er to logins.
# Runde 22 (Codex P1): den fikk først medlemskap i modul_eier «WITH
# INHERIT FALSE». Det var for bredt. Eierrollen eier BEGGE sider —
# attestfunksjonene OG registrer_moduldrill/aksepter_moduldeployment —
# og en eier har EXECUTE i kraft av eierskapet. `SET ROLE` var altså en
# åpen dør til hele akseptveien, og fire-øyne-skillet lå bare i at
# skriptet ikke gikk gjennom den. Verifikatoren får nå EXECUTE direkte
# på de to attestfunksjonene (migrasjon 049) og ingen vei til
# eierrollen.
VERIFIKATOR=disponit_ci_verifikator
# 090 (M-10) / 091 (M-11): driftstatusens lesejobb og selvtesten har HVER
# SIN DB-rolle — varselsender-modellen, ordrett. Begge er LOGIN med
# tilfeldig passord og faar sin egen DSN i miljoefila, som $VARSLER og
# $PLANARB: enhetene leser den gjennom LoadCredential, aldri gjennom
# API-ets.
#
# Hver rolle har noeyaktig EN rettighet (migrer.py
# DRIFTSTATUS_RETTIGHETER/SELVTEST_RETTIGHETER): EXECUTE paa sin
# skrivedoer. Ingen tabellrettigheter, ingen lesedoer, ingen sveip.
# Fravaeret av alt annet ER rollen: en kompromittert lesejobb kan dikte
# en verifisering som ikke har skjedd, og en kompromittert selvtest kan
# dikte en groenn runde — og det er alt hver av dem kan. Delte de DSN
# med API-et, ville skrivedoerene maattet grantes til `disponit`, og
# hele foresporselsveien kunne skrevet backuphistorikk og selvtestrunder.
DRIFTSTATUS=disponit_driftstatus
SELVTEST=disponit_selvtest
# KLYNGEN «orden i eget hus» (092-096). Rollene opprettes HER, i
# fundamentet, og ikke én om gangen med hver modul-PR. Grunnen er
# konkret og nylig: #324 la to nye roller i migrasjonene, verten hadde
# dem ikke, og HVER påfølgende deploy stoppet på deployporten til dette
# skriptet var kjørt. Fem moduler som lander etter tur ville gitt fem
# slike stopp. Én rolleoppretting i forkant gir null.
#
# En rolle uten tabeller er ufarlig: NOLOGIN-eierne eier ingenting før
# migrasjonen deres kjører, og LOGIN-rollene har ingen rettigheter før
# migrer.py gir dem noen (den ene EXECUTE-en hver, m10/m11-formen).
# Fraværet av alt annet ER rollen.
#
# Fem eiere, tre målere — og M-21 har MED VILJE ingen egen LOGIN:
# fristsveipen er et forpass i varselsenderen, ikke en ny timer, så den
# kjører som $VARSLER. En ny varslingsvei er en ny vei å miste et varsel i.
KVALITETEIER=disponit_kvalitet_eier      # M-3 eier profil- og funnlagrene
KVALITETSMAALER=disponit_kvalitetsmaaler # M-3s profileringsjobb
LAGEREIER=disponit_lager_eier            # M-4 eier retensjonsregisteret
LAGERMAALER=disponit_lagermaaler         # M-4s beholdningsmåling
MALEIER=disponit_mal_eier                # M-5 eier malregisteret (ingen jobb)
KUNNSKAPEIER=disponit_kunnskap_eier      # M-9 eier begrepsregisteret
KUNNSKAPSSVEIP=disponit_kunnskapssveip   # M-9s utløpssveip
PLIKTEIER=disponit_plikt_eier            # M-21 eier forpliktelsesregisteret
# KLYNGE 2 (097-100), samme forhåndsoppretting som klynge 1 og av samme
# grunn: én kjøring av dette skriptet framfor fire stoppede deployer.
# M-22 har MED VILJE ingen egen LOGIN — utløpssveipen er et forpass i
# varselsenderen (M-21-formen), så den kjører som $VARSLER.
TILGANGEIER=disponit_tilgang_eier        # M-12 eier tilgangsregisteret
TILGANGSSVEIP=disponit_tilgangssveip     # M-12s gjennomgangssveip
LISENSEIER=disponit_lisens_eier          # M-22 eier lisensregisteret
PERSONVERNEIER=disponit_personvern_eier  # M-30 eier forespørselsregisteret
PERSONVERNSVEIP=disponit_personvernsveip # M-30s fristsveip
COMPLIANCEEIER=disponit_compliance_eier  # M-34 eier kontrollregisteret
COMPLIANCESVEIP=disponit_compliancesveip # M-34s etterprøvingssveip
# KLYNGE 3 (101-105), samme forhåndsoppretting og av samme grunn: én
# kjøring av dette skriptet framfor fem stoppede deployer.
#
# HVER MODUL FÅR SIN EGEN SVEIPEROLLE, og det er en sikkerhetsdom og
# ikke en forglemmelse. En delt sveiperolle måtte hatt EXECUTE på alle
# fem kryss-tenant-defienerne, og en feil i én sveip ville da båret de
# fire andres fullmakt. Prisen er fem timere i stedet for én; den prisen
# er operasjonell, mens gevinsten er at hver sveips autoritet står i
# nøyaktig én definer, revidérbar på ett sted.
AVSTEMMINGEIER=disponit_avstemming_eier    # M-13 eier avstemmingsregisteret
AVSTEMMINGSVEIP=disponit_avstemmingssveip  # M-13s aldringssveip
KUNDESERVICEEIER=disponit_kundeservice_eier # M-17 eier henvendelsesregisteret
HENVENDELSESVEIP=disponit_henvendelsessveip # M-17s ubesvart-sveip
ONBOARDINGEIER=disponit_onboarding_eier    # M-18 eier onboardingregisteret
ONBOARDINGSVEIP=disponit_onboardingsveip   # M-18s stoppet-løp-sveip
FORDRINGEIER=disponit_fordring_eier        # M-23 eier fordringsregisteret
FORDRINGSVEIP=disponit_fordringssveip      # M-23s forfallssveip
LEVERANDOREIER=disponit_leverandor_eier    # M-24 eier leverandørregisteret
LEVERANDORSVEIP=disponit_leverandorsveip   # M-24s SLA-sveip
# KLYNGE 4 (106-110), samme forhåndsoppretting og av samme grunn: én
# kjøring av dette skriptet framfor fem stoppede deployer.
#
# Med denne klyngen er plattformen oppe i FJORTEN nattlige sveip. Det
# tallet er nå stort nok til at planlegging og observerbarhet er en egen
# driftssak — men det er fortsatt ikke en grunn til å slå sveiperollene
# sammen. En delt rolle måtte hatt EXECUTE på alle kryss-tenant-
# defienerne, og en feil i én sveip ville da båret alle de andres
# fullmakt. Å bytte en driftssak mot en sikkerhetssvekkelse er ikke en
# forenkling.
FAKTURAEIER=disponit_faktura_eier          # M-14 eier fakturaregisteret
FAKTURASVEIP=disponit_fakturasveip         # M-14s ukontrollert-sveip
PROSJEKTEIER=disponit_prosjekt_eier        # M-25 eier prosjektregisteret
PROSJEKTSVEIP=disponit_prosjektsveip       # M-25s budsjettsveip
PRISBOKEIER=disponit_prisbok_eier          # M-26 eier prisboka
PRISBOKSVEIP=disponit_prisboksveip         # M-26s utlopt-pris-sveip
# `disponit_lager_eier` ER M-4s (retensjonsregisteret, migrasjon 093 og
# 099), og shellvariabelen LAGEREIER er alt i bruk lenger opp. M-27 får
# derfor sitt eget navn. Det er ikke kosmetikk: to moduler som deler
# eierrolle er nøyaktig den fullmaktsdelingen «én rolle per modul»
# finnes for å hindre — og `CREATE ROLE` to ganger hadde dessuten gjort
# CI rød. (CodeRabbit, klynge 4-fundamentet.)
BEHOLDNINGEIER=disponit_beholdning_eier    # M-27 eier lagerregisteret
LAGERSVEIP=disponit_lagersveip             # M-27s bestillingspunktsveip
KONTOVAKTEIER=disponit_kontovakt_eier      # M-42 eier kontoregisteret
KONTOVAKTSVEIP=disponit_kontovaktsveip     # M-42s kontoendringssveip
# KLYNGE 5 (111-114), samme forhåndsoppretting og av samme grunn.
#
# Med denne klyngen er plattformen oppe i ATTEN nattlige sveip
# (03:15 → 08:20). Driftssaken fra klynge 4 står og er tyngre — men
# fortsatt ikke en grunn til å slå sveiperollene sammen.
BETALINGEIER=disponit_betaling_eier         # M-41 eier betalingsregisteret
BETALINGSSVEIP=disponit_betalingssveip      # M-41s uavklart-betaling-sveip
ADRESSEEIER=disponit_adresse_eier           # M-19 eier adresseregisteret
ADRESSESVEIP=disponit_adressesveip          # M-19s ukontrollert-adresse-sveip
LONNEIER=disponit_lonn_eier                 # M-39 eier lonnsgrunnlaget
LONNSSVEIP=disponit_lonnssveip              # M-39s avvik-mot-plan-sveip
KAMPANJEEIER=disponit_kampanje_eier         # M-44 eier kampanjeregisteret
KAMPANJESVEIP=disponit_kampanjesveip        # M-44s frekvenstak-sveip
# KLYNGE 6 (116-120) — «de fem som finner noe, og ikke handler på det».
# Fem eiere og fem sveipere, av samme grunn som før: en delt sveiperolle
# måtte hatt EXECUTE på alle kryss-tenant-definerne, og en feil i én
# sveip ville båret de andres fullmakt.
MOTPARTEIER=disponit_motpart_eier           # M-48 eier motpartsregisteret
MOTPARTSSVEIP=disponit_motpartssveip        # M-48s forverring-sveip
SANKSJONEIER=disponit_sanksjon_eier         # M-49 eier sanksjonskontrollen
SANKSJONSSVEIP=disponit_sanksjonssveip      # M-49s uavklart-treff-sveip
ANBUDEIER=disponit_anbud_eier               # M-46 eier anbudsregisteret
ANBUDSSVEIP=disponit_anbudssveip            # M-46s frist-sveip
TILSKUDDEIER=disponit_tilskudd_eier         # M-51 eier tilskuddsregisteret
TILSKUDDSSVEIP=disponit_tilskuddssveip      # M-51s ordningsfrist-sveip
MERKEVAREIER=disponit_merkevare_eier        # M-55 eier merkevarefunnene
MERKEVARESVEIP=disponit_merkevaresveip      # M-55s ubehandlet-funn-sveip
# Klynge 7 (121-125): regelen er myndighetens.
EHFEIER=disponit_ehf_eier                   # M-54 eier EHF-avviksregisteret
EHFSVEIP=disponit_ehfsveip                  # M-54s utlopt-skjema-sveip
TOLLKODEEIER=disponit_tollkode_eier         # M-52 eier tollkoderegisteret
TOLLKODESVEIP=disponit_tollkodesveip        # M-52s utlopt-nomenklatur-sveip
MYNDIGHETEIER=disponit_myndighet_eier       # M-47 eier pliktregisteret
MYNDIGHETSSVEIP=disponit_myndighetssveip    # M-47s fristsveip
POSTJOURNALEIER=disponit_postjournal_eier   # M-50 eier journalregisteret
POSTJOURNALSVEIP=disponit_postjournalsveip  # M-50s formaal-sveip
HMSEIER=disponit_hms_eier                   # M-53 eier avviksregisteret
HMSSVEIP=disponit_hmssveip                  # M-53s ubehandlet-avvik-sveip
for r in "$BRUKER" "$MIGRATOR" "$TOKENADMIN" "$ARBEIDER" "$EGRESS" \
         "$DOMENER" "$VARSLER" "$PLANARB" "$VERIFIKATOR" "$DRIFTSTATUS" \
         "$SELVTEST" "$KVALITETSMAALER" "$LAGERMAALER" "$KUNNSKAPSSVEIP" \
         "$TILGANGSSVEIP" "$PERSONVERNSVEIP" "$COMPLIANCESVEIP" \
         "$AVSTEMMINGSVEIP" "$HENVENDELSESVEIP" "$ONBOARDINGSVEIP" \
         "$FORDRINGSVEIP" "$LEVERANDORSVEIP" "$FAKTURASVEIP" \
         "$PROSJEKTSVEIP" "$PRISBOKSVEIP" "$LAGERSVEIP" \
         "$KONTOVAKTSVEIP" "$BETALINGSSVEIP" "$ADRESSESVEIP" \
         "$LONNSSVEIP" "$KAMPANJESVEIP" "$MOTPARTSSVEIP" \
         "$SANKSJONSSVEIP" "$ANBUDSSVEIP" "$TILSKUDDSSVEIP" \
         "$MERKEVARESVEIP" "$EHFSVEIP" "$TOLLKODESVEIP" \
         "$MYNDIGHETSSVEIP" "$POSTJOURNALSVEIP" "$HMSSVEIP"; do
  sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$r'" \
    | grep -q 1 || sudo -u postgres psql -c \
    "CREATE ROLE $r LOGIN PASSWORD '$(openssl rand -hex 24)'"
done
for r in "$AUTH" "$M37" "$POLICYEIER" "$MODULEIER" "$MODULESADMIN" \
         "$DOMAINSADMIN" "$KVALITETEIER" "$LAGEREIER" "$MALEIER" \
         "$KUNNSKAPEIER" "$PLIKTEIER" "$TILGANGEIER" "$LISENSEIER" \
         "$PERSONVERNEIER" "$COMPLIANCEEIER" "$AVSTEMMINGEIER" \
         "$KUNDESERVICEEIER" "$ONBOARDINGEIER" "$FORDRINGEIER" \
         "$LEVERANDOREIER" "$FAKTURAEIER" "$PROSJEKTEIER" \
         "$PRISBOKEIER" "$BEHOLDNINGEIER" "$KONTOVAKTEIER" \
         "$BETALINGEIER" "$ADRESSEEIER" "$LONNEIER" "$KAMPANJEEIER" \
         "$MOTPARTEIER" "$SANKSJONEIER" "$ANBUDEIER" "$TILSKUDDEIER" \
         "$MERKEVAREIER" "$EHFEIER" "$TOLLKODEEIER" \
         "$MYNDIGHETEIER" "$POSTJOURNALEIER" "$HMSEIER"; do
  sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$r'" \
    | grep -q 1 || sudo -u postgres psql -qc "CREATE ROLE $r NOLOGIN"
done
# domene_eier eier de kryss-tenant takeover-funksjonene → BYPASSRLS (den maa se
# andre tenanters domenekontroll-rader via hostname_binding-autoriteten).
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$DOMENEEIER'" \
  | grep -q 1 || sudo -u postgres psql -qc "CREATE ROLE $DOMENEEIER NOLOGIN BYPASSRLS"
# Migrator maa vaere MEDLEM av begge for aa kunne sette eierskap (OWNER TO)
# paa api_tokener (003) og paa arbeidskapabiliteter + M-37-funksjonene (005).
sudo -u postgres psql -qc "GRANT $AUTH TO $MIGRATOR"
# WITH INHERIT FALSE er ikke pynt. Migrator maa vaere MEDLEM for aa kunne
# gjoere OWNER TO, men et vanlig medlemskap gir ogsaa ARVEDE rettigheter —
# og RLS-policyer med TO-klausul matcher paa arvet medlemskap. Uten dette
# arvet migrator M-37-dispatcherens policy paa revisjonslogg og unntak, og
# saa igjen alle tenanters rader. Det er Codex' P1 nr. 2 fra PR-004 paa
# nytt, gjeninnfoert av en GRANT som saa ut som en formalitet.
# (PostgreSQL 16+. SET ROLE er fortsatt mulig for migrator — det er en
# eksplisitt, sporbar handling, ikke stille arv.)
sudo -u postgres psql -qc "GRANT $M37 TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $POLICYEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $MODULEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $MODULESADMIN TO $MIGRATOR WITH INHERIT FALSE"
# Verifikatoren får INGEN rollemedlemskap — se blokken over LOGIN-løkka.
# Fullmakten er de to EXECUTE-ene migrasjon 049 gir den, og ikke noe mer.
# REVOKE-en er ikke pynt: baser satt opp med runde 21-varianten har alt
# medlemskapet, og oppsettet er idempotent nettopp for å ta dem igjen.
sudo -u postgres psql -qc "REVOKE $MODULEIER FROM $VERIFIKATOR"
sudo -u postgres psql -qc "GRANT $DOMENEEIER TO $MIGRATOR WITH INHERIT FALSE"
# 041: adjudikatorrollen — klyngeobjekt som rollene over. Runtime faar SET
# (aldri arv) for de to lesningene i adjudikasjonsendepunktene; migrator
# faar medlemskap for rebuilds/tester. Policyen i 041 avgrenser radene.
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$ADJUDIKATOR'" \
  | grep -q 1 || sudo -u postgres psql -qc "CREATE ROLE $ADJUDIKATOR NOLOGIN"
sudo -u postgres psql -qc "GRANT $ADJUDIKATOR TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $ADJUDIKATOR TO $BRUKER WITH INHERIT FALSE, SET TRUE"
sudo -u postgres psql -qc "GRANT $DOMAINSADMIN TO $MIGRATOR WITH INHERIT FALSE"
# KLYNGEN «orden i eget hus» (#326 opprettet rollene, men glemte dette).
# De fem eierrollene trenger NØYAKTIG det de fire eldre eierrollene har,
# og av samme to grunner:
#   * MEDLEMSKAP, fordi migrasjonen gjør `SET LOCAL ROLE <eier>` og
#     `OWNER TO <eier>`. Uten det svarer migrasjonen «permission denied
#     to set role» — og den feiler MIDT i kjeden, ikke i en port.
#   * WITH INHERIT FALSE, fordi et vanlig medlemskap også gir ARVEDE
#     rettigheter, og RLS-policyer med TO-klausul matcher på arvet
#     medlemskap. Det er Codex' P1 nr. 2 fra PR-004, og den feilen er
#     gjeninnført av en GRANT som så ut som en formalitet før. Den skal
#     ikke gjeninnføres av at fem nye roller kom inn uten den.
# De tre MÅLERROLLENE får INGENTING her: de er LOGIN-jobber med én
# EXECUTE hver (migrer.py), aldri eierskap og aldri SET ROLE.
sudo -u postgres psql -qc "GRANT $KVALITETEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $LAGEREIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $MALEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $KUNNSKAPEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $PLIKTEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $TILGANGEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $LISENSEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $PERSONVERNEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $COMPLIANCEEIER TO $MIGRATOR WITH INHERIT FALSE"
# KLYNGE 3 (101-105): samme to grunner, ordrett. Sveiperollene får
# INGENTING her — de er LOGIN-jobber med én EXECUTE hver (migrer.py),
# aldri eierskap og aldri SET ROLE.
sudo -u postgres psql -qc "GRANT $AVSTEMMINGEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $KUNDESERVICEEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $ONBOARDINGEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $FORDRINGEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $LEVERANDOREIER TO $MIGRATOR WITH INHERIT FALSE"
# KLYNGE 4 (106-110). SAMME KRAV, OG DET ER IKKE FORMALIA: hver av disse
# migrasjonene gjoer `SET LOCAL ROLE <eier>` for aa lage doerene sine, og
# `SET ROLE` krever MEDLEMSKAP — ikke bare at rollen finnes. Uten linjene
# her stopper migrasjonen paa «permission denied to set role», og fordi
# basen da alt har flyttet seg forbi forrige release, nekter
# selv-reverseringen og enhetene blir staaende stoppet.
#
# Det skjedde 3/9: rollene ble opprettet av loekka over, ci.yml hadde
# medlemskapene, og verten hadde dem ikke. CI var gronn paa alle fem
# modulene mens verten aldri kunne kjoere dem.
#
# `test_deploy_rollemedlemskap.py` binder dette nedenfra: hver rolle en
# MIGRASJON bytter til, maa staa her.
sudo -u postgres psql -qc "GRANT $FAKTURAEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $PROSJEKTEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $PRISBOKEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $BEHOLDNINGEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $KONTOVAKTEIER TO $MIGRATOR WITH INHERIT FALSE"
# KLYNGE 5. Medlemskapene står HER, i samme commit som rollene — det er
# hele lærdommen fra 3/9: klynge 4 opprettet rollene og glemte
# medlemskapene, `SET ROLE` feilet midt i migrasjonssettet, basen sto
# mellom to releaser og enhetene ble stående stoppet (#361).
sudo -u postgres psql -qc "GRANT $BETALINGEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $ADRESSEEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $LONNEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $KAMPANJEEIER TO $MIGRATOR WITH INHERIT FALSE"
# KLYNGE 6: migrator må være MEDLEM av hver eierrolle for å kunne kjøre
# 116-120. `SET ROLE` krever MEDLEMSKAP, ikke at rollen finnes — det var
# nettopp den forskjellen som tok staging ned 3/9, og lista her og i
# `ci.yml` må derfor være den samme (#361 måler begge veier).
sudo -u postgres psql -qc "GRANT $MOTPARTEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $SANKSJONEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $ANBUDEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $TILSKUDDEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $MERKEVAREIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $EHFEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $TOLLKODEEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $MYNDIGHETEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $POSTJOURNALEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $HMSEIER TO $MIGRATOR WITH INHERIT FALSE"

sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='$DB'" \
  | grep -q 1 || sudo -u postgres createdb -O $MIGRATOR $DB

# Test-database for staging-kjøringer
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB}_test'" \
  | grep -q 1 || sudo -u postgres createdb -O $MIGRATOR ${DB}_test

# ------------------------------------------------------------
# Miljøfil med hemmeligheter — 0600, aldri i repo (RUTINER/ADR-001).
# Tilstandsmaskinen ligger i lib-miljofil.sh og testes i CI; her kalles den
# bare. Alle fem feilene som er funnet i dette oppsettet satt i den logikken,
# og manuell staging-prøve er ingen port.
# ------------------------------------------------------------
mkdir -p /etc/disponit && chmod 700 /etc/disponit
# m_wcag_audit-arbeideren leser /etc/disponit/wcag/ SELV (kontekst +
# kvitteringsnøkkel er gruppelesbare der) — men uten x på FORELDEREN når
# den aldri frem: 700 her ga PermissionError på en fil den hadde rett til.
# Traverse-only for arbeiderens gruppe (710, ingen r: katalogen kan ikke
# listes, og hver hemmelighet under beholder sin egen stramme modus).
# Betinget: brukeren opprettes av opp.sh, som kjører ETTER dette skriptet
# ved førstegangsoppsett — da tar neste kjøring (hver deploy) den igjen.
if id disponit-wcag >/dev/null 2>&1; then
  chgrp disponit-wcag /etc/disponit && chmod 710 /etc/disponit
fi
touch "$MILJOFIL" && chmod 600 "$MILJOFIL"

. "$(dirname "$0")/lib-miljofil.sh"

RUNTIME_DSN=("DATABASE_URL=$DB" "DISPONIT_TEST_DSN=${DB}_test")
MIGRATOR_DSN=("DISPONIT_MIGRATOR_URL=$DB" "DISPONIT_TEST_MIGRATOR_DSN=${DB}_test")
TOKENADMIN_DSN=("DISPONIT_TOKEN_ADMIN_URL=$DB"
                "DISPONIT_TEST_TOKEN_ADMIN_DSN=${DB}_test")
ARBEIDER_DSN=("DISPONIT_ARBEIDER_URL=$DB"
              "DISPONIT_TEST_ARBEIDER_DSN=${DB}_test")
# PR-014b (Codex P2): egress-proxyen har egen LOGIN-rolle med tilfeldig passord,
# men uten en DSN i miljøfilen kunne den ikke autentisere på en fersk install
# (manuell passord-reset kreves ellers). Samme state-machine som de andre rollene.
EGRESS_DSN=("DISPONIT_EGRESS_URL=$DB" "DISPONIT_TEST_EGRESS_DSN=${DB}_test")
# PR-015: driftstimerne (revalidering + rydding) leser DISPONIT_DOMAINS_URL —
# samme state-machine som de andre rollene, ellers kan ikke $DOMENER
# autentisere paa en fersk install.
DOMENER_DSN=("DISPONIT_DOMAINS_URL=$DB" "DISPONIT_TEST_DOMAINS_DSN=${DB}_test")
VARSLER_DSN=("DISPONIT_VARSEL_URL=$DB" "DISPONIT_TEST_VARSEL_DSN=${DB}_test")
PLANARB_DSN=("DISPONIT_PLAN_URL=$DB" "DISPONIT_TEST_PLAN_DSN=${DB}_test")
VERIFIKATOR_DSN=("DISPONIT_VERIFIKATOR_URL=$DB" "DISPONIT_TEST_VERIFIKATOR_DSN=${DB}_test")
# 090/091: driftstatusens og selvtestens DSN-er — samme state-machine som
# rollene over, ellers kan de ikke autentisere paa en fersk install.
DRIFTSTATUS_DSN=("DISPONIT_DRIFTSTATUS_URL=$DB"
                 "DISPONIT_TEST_DRIFTSTATUS_DSN=${DB}_test")
SELVTEST_DSN=("DISPONIT_SELVTEST_URL=$DB"
              "DISPONIT_TEST_SELVTEST_DSN=${DB}_test")
# Klyngens tre målere. Samme state-machine som rollene over: uten en DSN
# i miljøfila kan de ikke autentisere på en fersk install, og da stopper
# deployen når modulen deres lander — akkurat som #324 gjorde.
KVALITETSMAALER_DSN=("DISPONIT_KVALITETSMAALER_URL=$DB"
                     "DISPONIT_TEST_KVALITETSMAALER_DSN=${DB}_test")
LAGERMAALER_DSN=("DISPONIT_LAGERMAALER_URL=$DB"
                 "DISPONIT_TEST_LAGERMAALER_DSN=${DB}_test")
KUNNSKAPSSVEIP_DSN=("DISPONIT_KUNNSKAPSSVEIP_URL=$DB"
                    "DISPONIT_TEST_KUNNSKAPSSVEIP_DSN=${DB}_test")
TILGANGSSVEIP_DSN=("DISPONIT_TILGANGSSVEIP_URL=$DB"
                   "DISPONIT_TEST_TILGANGSSVEIP_DSN=${DB}_test")
PERSONVERNSVEIP_DSN=("DISPONIT_PERSONVERNSVEIP_URL=$DB"
                     "DISPONIT_TEST_PERSONVERNSVEIP_DSN=${DB}_test")
COMPLIANCESVEIP_DSN=("DISPONIT_COMPLIANCESVEIP_URL=$DB"
                     "DISPONIT_TEST_COMPLIANCESVEIP_DSN=${DB}_test")

# DE TI SOM MANGLET (#324s lærdom, ikke lært ferdig). Kommentaren over
# sier det allerede: en sveiperolle uten DSN i miljøfila kan ikke
# autentisere på en fersk install, og da STOPPER deployen når modulen
# lander. Rollene ble opprettet for hver av disse — men DSN-en ble aldri
# skrevet, fra og med M-13.
#
# `opp.sh` krevde fjorten sveip-DSN-er; dette skriptet skrev fire. En
# utrulling på en fersk maskin stoppet altså på den FØRSTE manglende, og
# har gjort det siden M-13, uten at noen port sa fra.
#
# `test_deploy_sveip_dsn.py` binder nå de to listene til hverandre, så
# den ellevte sveipen ikke kan gjenta det.
# M-13
AVSTEMMINGSVEIP_DSN=("DISPONIT_AVSTEMMINGSVEIP_URL=$DB"
                     "DISPONIT_TEST_AVSTEMMINGSVEIP_DSN=${DB}_test")
# M-17
HENVENDELSESVEIP_DSN=("DISPONIT_HENVENDELSESVEIP_URL=$DB"
                      "DISPONIT_TEST_HENVENDELSESVEIP_DSN=${DB}_test")
# M-18
ONBOARDINGSVEIP_DSN=("DISPONIT_ONBOARDINGSVEIP_URL=$DB"
                     "DISPONIT_TEST_ONBOARDINGSVEIP_DSN=${DB}_test")
# M-23
FORDRINGSVEIP_DSN=("DISPONIT_FORDRINGSVEIP_URL=$DB"
                   "DISPONIT_TEST_FORDRINGSVEIP_DSN=${DB}_test")
# M-24
LEVERANDORSVEIP_DSN=("DISPONIT_LEVERANDORSVEIP_URL=$DB"
                     "DISPONIT_TEST_LEVERANDORSVEIP_DSN=${DB}_test")
# M-14
FAKTURASVEIP_DSN=("DISPONIT_FAKTURASVEIP_URL=$DB"
                  "DISPONIT_TEST_FAKTURASVEIP_DSN=${DB}_test")
# M-25
PROSJEKTSVEIP_DSN=("DISPONIT_PROSJEKTSVEIP_URL=$DB"
                   "DISPONIT_TEST_PROSJEKTSVEIP_DSN=${DB}_test")
# M-26
PRISBOKSVEIP_DSN=("DISPONIT_PRISBOKSVEIP_URL=$DB"
                  "DISPONIT_TEST_PRISBOKSVEIP_DSN=${DB}_test")
# M-27
LAGERSVEIP_DSN=("DISPONIT_LAGERSVEIP_URL=$DB"
                "DISPONIT_TEST_LAGERSVEIP_DSN=${DB}_test")
# M-42
KONTOVAKTSVEIP_DSN=("DISPONIT_KONTOVAKTSVEIP_URL=$DB"
                    "DISPONIT_TEST_KONTOVAKTSVEIP_DSN=${DB}_test")
# M-41
BETALINGSSVEIP_DSN=("DISPONIT_BETALINGSSVEIP_URL=$DB"
                    "DISPONIT_TEST_BETALINGSSVEIP_DSN=${DB}_test")
ADRESSESVEIP_DSN=("DISPONIT_ADRESSESVEIP_URL=$DB"
                  "DISPONIT_TEST_ADRESSESVEIP_DSN=${DB}_test")
LONNSSVEIP_DSN=("DISPONIT_LONNSSVEIP_URL=$DB"
                "DISPONIT_TEST_LONNSSVEIP_DSN=${DB}_test")
KAMPANJESVEIP_DSN=("DISPONIT_KAMPANJESVEIP_URL=$DB"
                   "DISPONIT_TEST_KAMPANJESVEIP_DSN=${DB}_test")
# M-48 (116). Rollen ble opprettet av klyngefundamentet (#371); DSN-en
# hører modul-PR-en til — arbeidsdelingen #360/#361 måler begge veier.
MOTPARTSSVEIP_DSN=("DISPONIT_MOTPARTSSVEIP_URL=$DB"
                   "DISPONIT_TEST_MOTPARTSSVEIP_DSN=${DB}_test")
# M-49 (117). Rollen ble opprettet av klyngefundamentet (#371).
SANKSJONSSVEIP_DSN=("DISPONIT_SANKSJONSSVEIP_URL=$DB"
                    "DISPONIT_TEST_SANKSJONSSVEIP_DSN=${DB}_test")
# M-46 (118). Rollen ble opprettet av klyngefundamentet (#371).
ANBUDSSVEIP_DSN=("DISPONIT_ANBUDSSVEIP_URL=$DB"
                 "DISPONIT_TEST_ANBUDSSVEIP_DSN=${DB}_test")
# M-51 (119). Rollen ble opprettet av klyngefundamentet (#371).
TILSKUDDSSVEIP_DSN=("DISPONIT_TILSKUDDSSVEIP_URL=$DB"
                    "DISPONIT_TEST_TILSKUDDSSVEIP_DSN=${DB}_test")
# M-55 (120). Rollen ble opprettet av klyngefundamentet (#371).
MERKEVARESVEIP_DSN=("DISPONIT_MERKEVARESVEIP_URL=$DB"
                    "DISPONIT_TEST_MERKEVARESVEIP_DSN=${DB}_test")
# M-54 (121). Rollen ble opprettet av klynge 7-fundamentet (#377).
EHFSVEIP_DSN=("DISPONIT_EHFSVEIP_URL=$DB"
              "DISPONIT_TEST_EHFSVEIP_DSN=${DB}_test")
# M-52 (122). Rollen ble opprettet av klynge 7-fundamentet (#377).
TOLLKODESVEIP_DSN=("DISPONIT_TOLLKODESVEIP_URL=$DB"
                   "DISPONIT_TEST_TOLLKODESVEIP_DSN=${DB}_test")

sikre_rolle_dsn "$BRUKER"     "${RUNTIME_DSN[@]}"
sikre_rolle_dsn "$MIGRATOR"   "${MIGRATOR_DSN[@]}"
sikre_rolle_dsn "$TOKENADMIN" "${TOKENADMIN_DSN[@]}"
sikre_rolle_dsn "$ARBEIDER"   "${ARBEIDER_DSN[@]}"
sikre_rolle_dsn "$EGRESS"     "${EGRESS_DSN[@]}"
sikre_rolle_dsn "$DOMENER"    "${DOMENER_DSN[@]}"
sikre_rolle_dsn "$VARSLER"    "${VARSLER_DSN[@]}"
sikre_rolle_dsn "$PLANARB"    "${PLANARB_DSN[@]}"
sikre_rolle_dsn "$VERIFIKATOR" "${VERIFIKATOR_DSN[@]}"
sikre_rolle_dsn "$DRIFTSTATUS" "${DRIFTSTATUS_DSN[@]}"
sikre_rolle_dsn "$SELVTEST"   "${SELVTEST_DSN[@]}"
sikre_rolle_dsn "$KVALITETSMAALER" "${KVALITETSMAALER_DSN[@]}"
sikre_rolle_dsn "$LAGERMAALER"     "${LAGERMAALER_DSN[@]}"
sikre_rolle_dsn "$KUNNSKAPSSVEIP"  "${KUNNSKAPSSVEIP_DSN[@]}"
sikre_rolle_dsn "$TILGANGSSVEIP"    "${TILGANGSSVEIP_DSN[@]}"
sikre_rolle_dsn "$PERSONVERNSVEIP" "${PERSONVERNSVEIP_DSN[@]}"
sikre_rolle_dsn "$COMPLIANCESVEIP" "${COMPLIANCESVEIP_DSN[@]}"
sikre_rolle_dsn "$AVSTEMMINGSVEIP" "${AVSTEMMINGSVEIP_DSN[@]}"
sikre_rolle_dsn "$HENVENDELSESVEIP" "${HENVENDELSESVEIP_DSN[@]}"
sikre_rolle_dsn "$ONBOARDINGSVEIP" "${ONBOARDINGSVEIP_DSN[@]}"
sikre_rolle_dsn "$FORDRINGSVEIP" "${FORDRINGSVEIP_DSN[@]}"
sikre_rolle_dsn "$LEVERANDORSVEIP" "${LEVERANDORSVEIP_DSN[@]}"
sikre_rolle_dsn "$FAKTURASVEIP" "${FAKTURASVEIP_DSN[@]}"
sikre_rolle_dsn "$PROSJEKTSVEIP" "${PROSJEKTSVEIP_DSN[@]}"
sikre_rolle_dsn "$PRISBOKSVEIP" "${PRISBOKSVEIP_DSN[@]}"
sikre_rolle_dsn "$LAGERSVEIP" "${LAGERSVEIP_DSN[@]}"
sikre_rolle_dsn "$KONTOVAKTSVEIP" "${KONTOVAKTSVEIP_DSN[@]}"
sikre_rolle_dsn "$BETALINGSSVEIP" "${BETALINGSSVEIP_DSN[@]}"
sikre_rolle_dsn "$ADRESSESVEIP" "${ADRESSESVEIP_DSN[@]}"
sikre_rolle_dsn "$LONNSSVEIP" "${LONNSSVEIP_DSN[@]}"
sikre_rolle_dsn "$KAMPANJESVEIP" "${KAMPANJESVEIP_DSN[@]}"
sikre_rolle_dsn "$MOTPARTSSVEIP" "${MOTPARTSSVEIP_DSN[@]}"
sikre_rolle_dsn "$SANKSJONSSVEIP" "${SANKSJONSSVEIP_DSN[@]}"
sikre_rolle_dsn "$ANBUDSSVEIP" "${ANBUDSSVEIP_DSN[@]}"
sikre_rolle_dsn "$TILSKUDDSSVEIP" "${TILSKUDDSSVEIP_DSN[@]}"
sikre_rolle_dsn "$MERKEVARESVEIP" "${MERKEVARESVEIP_DSN[@]}"
sikre_rolle_dsn "$EHFSVEIP" "${EHFSVEIP_DSN[@]}"
sikre_rolle_dsn "$TOLLKODESVEIP" "${TOLLKODESVEIP_DSN[@]}"
sikre_attestasjonsnokler
sikre_mac_nokler          # PR-012: MAC-register (oppstartsperre for API-et)
# KEK og token-pepper (PR-005b). KEK manglet helt etter PR-005a: krypteringen
# ble innfoert, men ingen deploy-vei satte noekkelen — API-et nekter aa starte
# uten den, og det er slik feilen skal oppdages, ikke i foerste unntaksrad.
sikre_hex_hemmelighet DISPONIT_KEK 32
sikre_hex_hemmelighet DISPONIT_TOKEN_PEPPER 32

# Sannhetsprøve FØR noen DSN tas i bruk.
#
# Codex' P1: denne sto tidligere bare til slutt. Var forrige kjøring avbrutt
# mellom passordrotasjon og filskriving, pekte migrator-DSN-en på et passord
# rollen ikke lenger hadde — migrasjonen feilet, `set -e` avsluttet skriptet,
# og reparasjonen ble aldri nådd. Reparasjonen var altså utilgjengelig
# nøyaktig i den tilstanden den fantes for. Den må kjøre før første bruk.
verifiser_og_reparer "$BRUKER"     "${RUNTIME_DSN[@]}"
verifiser_og_reparer "$MIGRATOR"   "${MIGRATOR_DSN[@]}"
verifiser_og_reparer "$TOKENADMIN" "${TOKENADMIN_DSN[@]}"
verifiser_og_reparer "$ARBEIDER"   "${ARBEIDER_DSN[@]}"
verifiser_og_reparer "$EGRESS"     "${EGRESS_DSN[@]}"
verifiser_og_reparer "$DOMENER"    "${DOMENER_DSN[@]}"
verifiser_og_reparer "$VARSLER"    "${VARSLER_DSN[@]}"
verifiser_og_reparer "$PLANARB"    "${PLANARB_DSN[@]}"
verifiser_og_reparer "$VERIFIKATOR" "${VERIFIKATOR_DSN[@]}"
verifiser_og_reparer "$DRIFTSTATUS" "${DRIFTSTATUS_DSN[@]}"
verifiser_og_reparer "$SELVTEST"   "${SELVTEST_DSN[@]}"

# ------------------------------------------------------------
# Migrasjoner kjøres av MIGRATOR-rollen — verken av postgres eller av
# runtime.
#
#   postgres  => tabellene eies av superbrukeren, og ingen andre kan
#                migrere skjemaet ("must be owner of table revisjonslogg").
#   runtime   => runtime kan skru av eller slette append-only-triggerne
#                sine egne. Codex' P1 i PR-004-reviewen.
#
# Runtime får kun DML: SELECT og INSERT. Ingen UPDATE, ingen DELETE, ingen
# TRUNCATE, ingen eierskap — append-only er da ikke bare en trigger, men
# også et fravær av rettigheter.
# ------------------------------------------------------------
cd /opt/disponit || cd "$(dirname "$0")/../.."
set -a; . "$MILJOFIL"; set +a

for base in $DB ${DB}_test; do
  # Flytt eierskap til migrator (reparerer tidligere installasjoner der
  # objektene ble eid av postgres eller av runtime-rollen).
  # Codex' P1: forrige versjon skrev «ALTER FUNCTION public.navn()» uten
  # argumenttyper. Det er feil for enhver funksjon med parametre, og det
  # ville i tillegg forsøkt å ta eierskap over funksjoner som tilhører en
  # EXTENSION — pgcrypto er allerede tilgjengelig her og trengs til
  # attestasjonssignaturene. Å endre eier på extension-objekter er både
  # unødvendig og skadelig.
  #
  # PR-008-staging fant NESTE hull i den gamle reparasjonen: den flyttet
  # OGSÅ objektene migrasjonene BEVISST eier med andre roller, og på en
  # allerede migrert base flatet en re-kjøring rollemodellen ut. Første
  # fiks allowlistet ROLLENE — Codex' P1: da bevares også FEILPLASSERTE
  # ordinære objekter hos de privilegerte rollene, og manglende
  # designobjekter (staging manglet fem claimer-funksjoner) synes aldri.
  #
  # Reparasjonen er nå OBJEKTSPESIFIKK og bor i eierskap-reparasjon.sql:
  # designtabellen der lister nøyaktig hva authenticator og m37_claimer
  # skal eie (speilet fra 003/004/005/007), alt annet går til migrator,
  # og en sluttkontroll feiler hardt hvis noe står utenfor modellen.
  # Testene i platform/core/tests/test_eierskap.py kjører NØYAKTIG samme
  # fil begge veier (flatet designobjekt -> designeier, feilplassert
  # objekt hos privilegert rolle -> migrator).
  sudo -u postgres psql -q -v ON_ERROR_STOP=1 -d "$base" \
    -f "$(dirname "$0")/eierskap-reparasjon.sql"

  # VARSELENUMENE, samme klasse av reparasjon og samme grunn til at den
  # bor HER og ikke i kjeden: migrasjonen som feiler PAA driften (090)
  # kommer FOER en migrasjon som kunne rettet den. En base som alt er
  # kanonisk roeres ikke; en fersk base uten `varsel` hoppes over.
  sudo -u postgres psql -q -v ON_ERROR_STOP=1 -d "$base" \
    -f "$(dirname "$0")/varselenum-reparasjon.sql"

  # Selvhelbredelse: den forrige versjonen av dette skriptet rakk å ta
  # eierskap over extension-funksjoner UTEN argumenter (på staging traff den
  # pgcrypto sine `fips_mode()` og `gen_random_uuid()`). Gi dem tilbake til
  # extensionens egen eier, ellers henger skaden igjen på enhver maskin der
  # den gamle versjonen har kjørt.
  sudo -u postgres psql -qtAd "$base" -c \
    "SELECT format('ALTER FUNCTION %s OWNER TO %I;', p.oid::regprocedure,
                   pg_get_userbyid(e.extowner))
       FROM pg_proc p
       JOIN pg_namespace n ON n.oid=p.pronamespace
       JOIN pg_depend d ON d.classid='pg_proc'::regclass
                       AND d.objid=p.oid
                       AND d.refclassid='pg_extension'::regclass
                       AND d.deptype='e'
       JOIN pg_extension e ON e.oid=d.refobjid
      WHERE n.nspname='public'
        AND pg_get_userbyid(p.proowner) <> pg_get_userbyid(e.extowner)" \
    | sudo -u postgres psql -q -v ON_ERROR_STOP=1 -d "$base" -f -
  sudo -u postgres psql -q -d "$base" -c "ALTER SCHEMA public OWNER TO $MIGRATOR"
  # FERSK-SERVER-FUNN (disponit.com-maskinen): PostgreSQL ≥ 15 gir ingen
  # CREATE på public til andre enn skjemaeieren. 003/005/007 oppretter
  # objekter UNDER SET ROLE authenticator/m37_claimer — de rollene må ha
  # CREATE, ellers dør første migrasjonskjøring med «permission denied for
  # schema public». Gamle staging hadde grantene fra en manuell æra;
  # førstegangsveien hadde aldri satt dem selv.
  sudo -u postgres psql -q -d "$base" -c \
    "GRANT USAGE, CREATE ON SCHEMA public TO $AUTH, $M37, $POLICYEIER, $MODULEIER, $DOMENEEIER"
  # Samme grunn for klyngens fem eiere: migrasjonene 092-096 lager sine
  # objekter UNDER `SET LOCAL ROLE <eier>`, og uten CREATE på public dør
  # kjøringen med «permission denied for schema public».
  sudo -u postgres psql -q -d "$base" -c \
    "GRANT USAGE, CREATE ON SCHEMA public TO $KVALITETEIER, $LAGEREIER, $MALEIER, $KUNNSKAPEIER, $PLIKTEIER"
  sudo -u postgres psql -q -d "$base" -c \
    "GRANT USAGE, CREATE ON SCHEMA public TO $TILGANGEIER, $LISENSEIER, $PERSONVERNEIER, $COMPLIANCEEIER"
  # …og for klynge 3s fem eiere (101-105), av nøyaktig samme grunn.
  sudo -u postgres psql -q -d "$base" -c \
    "GRANT USAGE, CREATE ON SCHEMA public TO $AVSTEMMINGEIER, $KUNDESERVICEEIER, $ONBOARDINGEIER, $FORDRINGEIER, $LEVERANDOREIER"
done

# ------------------------------------------------------------
# Migrasjoner OG rettigheter kjøres av den herdede kjøreren, ikke av psql.
#
# Codex' P1: forrige versjon kjørte filene med `psql -f`. Det omgår
# advisory-låsen, transaksjonen kjøreren eier fra versjon 3, checksum-
# registreringen og avvisningen av endret historikk. En herdet kjører som
# omgås av sitt eget oppsettskript er ikke en kjører, den er en anbefaling.
#
# Rettighetene settes av samme skript, ETTER migrasjonene: en GRANT på en
# tabell som ikke finnes ennå er stille virkningsløs (det var feil nr. 6).
# ------------------------------------------------------------
VENV="/opt/disponit/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
fi
# PR-010: Authlib eier OIDC-protokollmekanikken (authorization code + PKCE,
# JWS/ID-token-validering, JWKS-rotasjon) — Disponit skriver ingen egen
# JWT-parser (v6 §3). joserfc er Authlibs JOSE-backend. Pinnes med hash i
# et lockfil som egen driftsoppgave; her installeres de i venv-en.
# PR-015: revalideringsarbeideren (`drift.kjor_revalidering`) slaar opp TXT
# gjennom dnspython. Importen er lat, saa uten den her ville unit-preflighten
# passert og feilen foerst dukket opp ved foerste timeraktivering — som en
# RuntimeError i `_txt_oppslag`, med ingen domener revalidert.
"$VENV/bin/pip" install -q "psycopg[binary]" cryptography pyyaml jsonschema pytest \
  starlette uvicorn httpx "authlib>=1.6,<2" joserfc dnspython

# ------------------------------------------------------------
# 048 (#108), Codex P1: VEDLIKEHOLDSVINDU FOR PLAN-ARBEIDEREN.
#
# Migrasjon 048 REVOKER claim/terminaliser/frigi_planvindu fra `disponit`
# og gir dem til `disponit_plan_arbeider`. Paa en vert som alt har rullet
# ut, kjoerer `disponit-plan.timer` hvert 5. minutt med credentialen fra
# FORRIGE opp.sh — altsaa runtime-DSN-en. Migrasjonen her og
# credential-byttet i opp.sh er to separate kommandoer, og i gapet mellom
# dem feiler HVER planaktivering paa `claim_planvindu`. Planvinduer hentes
# aldri inn igjen (SKIP-semantikken er bevisst), saa gapet skriver bort
# kontroller permanent — ogsaa naar de to kommandoene kjoeres etter
# hverandre slik rutinen sier.
#
# Revoken og credential-byttet hoerer derfor til SAMME operasjon:
# arbeideren stoppes FOER migrasjonene, credentialen dens skrives om til
# plan-arbeiderrollens DSN etterpaa, og foerst da startes timeren igjen.
# Vi starter kun det VI stoppet — er timeren ikke aktivert paa denne
# verten (fersk install), er det opp.sh som aktiverer den.
PLAN_TIMER_VAR_AKTIV=0
if command -v systemctl >/dev/null 2>&1 \
   && systemctl is-active --quiet disponit-plan.timer 2>/dev/null; then
  PLAN_TIMER_VAR_AKTIV=1
fi
# Timeren OG den aktive oneshot-tjenesten (opp.sh steg 5, samme grunn): aa
# stoppe timeren alene avbryter ikke en kjoering som alt er i gang, og
# `systemctl stop` paa en oneshot venter til prosessen er ute — vinduet
# aapnes foerst naar arbeideren faktisk er stille.
if command -v systemctl >/dev/null 2>&1; then
  systemctl stop disponit-plan.timer disponit-plan.service 2>/dev/null || true
fi

for _dsn in "$DISPONIT_MIGRATOR_URL" "$DISPONIT_TEST_MIGRATOR_DSN"; do
  DISPONIT_MIGRATOR_URL="$_dsn" "$VENV/bin/python" \
    "$(dirname "$0")/migrer.py" "$BRUKER"
done

# ------------------------------------------------------------
# GO-vilkaar V3, siste lag: migrator kan ikke DEAKTIVERE retention-vakten.
#
# BEFORE DELETE-triggeren i migrasjon 005 binder ENHVER rolle som gjoer
# DELETE — ogsaa migrator og skjemaeieren. Det finnes noeyaktig to veier
# forbi en radtrigger i PostgreSQL:
#
#   1. ALTER TABLE ... DISABLE TRIGGER   (kun eier — altsaa migrator)
#   2. session_replication_role='replica' (kun superbruker)
#
# Vei 2 krever en superbruker ingen tjeneste har. Vei 1 lukkes her, med en
# EVENT TRIGGER: den kan bare opprettes av superbrukeren, og migrator kan
# derfor verken fjerne den eller endre den. Den paastaar ingenting om
# syntaks — den sjekker TILSTANDEN etter enhver ALTER TABLE, saa den kan
# ikke omgaas ved aa formulere kommandoen annerledes.
#
# Residual, sagt rett ut: en bar `DROP TRIGGER policy_retention` fanges
# ikke her (da finnes ikke triggeren aa sjekke, og migrasjon 005 dropper og
# gjenoppretter den selv paa hver kjoering). Den veien fanges av
# sluttkontrollen i migrer.py, som nekter aa fullfoere et deploy uten en
# aktiv retention-vakt.
# ------------------------------------------------------------
for base in $DB ${DB}_test; do
  sudo -u postgres psql -q -v ON_ERROR_STOP=1 -d "$base" <<'SQL'
CREATE OR REPLACE FUNCTION disponit_vern_policy_retention()
RETURNS event_trigger LANGUAGE plpgsql AS $$
DECLARE v_status "char";
BEGIN
    IF to_regclass('public.policyer') IS NULL THEN
        RETURN;                      -- tabellen finnes ikke ennaa
    END IF;
    SELECT t.tgenabled INTO v_status
      FROM pg_trigger t
     WHERE t.tgrelid = 'public.policyer'::regclass
       AND t.tgname = 'policy_retention';
    IF FOUND AND v_status = 'D' THEN
        RAISE EXCEPTION
            'policy_retention er deaktivert — GO-vilkaar V3 forbyr det, ogsaa for migratorrollen. Bruk arkiver_policyversjon().';
    END IF;
END $$;
DROP EVENT TRIGGER IF EXISTS disponit_policy_retention_vern;
CREATE EVENT TRIGGER disponit_policy_retention_vern
    ON ddl_command_end WHEN TAG IN ('ALTER TABLE')
    EXECUTE FUNCTION disponit_vern_policy_retention();
SQL
done

# Sluttkontroll: alt skal fortsatt virke etter migrasjoner og rettigheter.
verifiser_og_reparer "$BRUKER"     "${RUNTIME_DSN[@]}"
verifiser_og_reparer "$MIGRATOR"   "${MIGRATOR_DSN[@]}"
verifiser_og_reparer "$TOKENADMIN" "${TOKENADMIN_DSN[@]}"
verifiser_og_reparer "$EGRESS"     "${EGRESS_DSN[@]}"
verifiser_og_reparer "$DOMENER"    "${DOMENER_DSN[@]}"
verifiser_og_reparer "$VARSLER"    "${VARSLER_DSN[@]}"
verifiser_og_reparer "$PLANARB"    "${PLANARB_DSN[@]}"
verifiser_og_reparer "$VERIFIKATOR" "${VERIFIKATOR_DSN[@]}"
verifiser_og_reparer "$DRIFTSTATUS" "${DRIFTSTATUS_DSN[@]}"
verifiser_og_reparer "$SELVTEST"   "${SELVTEST_DSN[@]}"

# ------------------------------------------------------------
# 048 (#108), Codex P1: LUKK VEDLIKEHOLDSVINDUET.
#
# Credentialen byttes HER, i samme operasjon som revoken — ikke foerst ved
# neste opp.sh. Miljoefila leses paa nytt: reparasjonene rett over kan ha
# rotert plan-rollens passord, og da er verdien vi leste foer
# migrasjonene utdatert. En tom verdi skrives aldri (samme kontrakt som
# opp.sh: en tom credential ville foerst vist seg som en feilende
# timerkjoering).
#
# Katalogen opprettes ikke her — den eies av opp.sh (`install -d -m 700
# /etc/disponit/plan`). Finnes den ikke, har verten aldri rullet ut, og da
# finnes det heller ingen levende timer som kan feile i gapet.
set -a; . "$MILJOFIL"; set +a
if [ -d /etc/disponit/plan ]; then
  if [ -z "${DISPONIT_PLAN_URL:-}" ]; then
    echo "AVBRUTT: DISPONIT_PLAN_URL mangler i $MILJOFIL etter oppsettet."
    echo "Migrasjonene har alt fjernet claim-EXECUTE fra runtime-rollen, og"
    echo "disponit-plan.timer er STOPPET. Rett miljøfila og kjør skriptet"
    echo "på nytt — timeren startes først når credentialen er byttet."
    exit 1
  fi
  printf '%s' "$DISPONIT_PLAN_URL" > /etc/disponit/plan/DISPONIT_DATABASE_URL
  chmod 600 /etc/disponit/plan/DISPONIT_DATABASE_URL
  echo "  skrev plan-arbeiderens DSN til /etc/disponit/plan/DISPONIT_DATABASE_URL"
fi
if [ "$PLAN_TIMER_VAR_AKTIV" -eq 1 ]; then
  systemctl start disponit-plan.timer
  echo "  startet disponit-plan.timer igjen (credentialen er byttet)"
fi

echo "OK. Kilde miljøet med: set -a; . $MILJOFIL; set +a"
echo "Verifiser: python3 -m pytest platform/core/tests -q"
