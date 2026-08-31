-- ============================================================
-- Eierskapsreparasjon — OBJEKTSPESIFIKK designmodell (FIX-009 runde 2).
--
-- Codex' P1 på første utgave: en reparasjon som allowlister ROLLENE
-- $AUTH/$M37 bevarer også FEILPLASSERTE ordinære objekter hos de
-- privilegerte rollene. Og den symmetriske feilen var målbar i den andre
-- retningen: den håndholdte gjenopprettingslisten på staging manglet fem
-- claimer-funksjoner (trigger- og adminfunksjonene 005/007 oppretter i
-- SET ROLE-vinduet) — en rolleliste kunne aldri ha oppdaget det.
--
-- Modellen er derfor DESIGNTABELLEN under: hvert objekt de privilegerte
-- rollene skal eie, med full signatur, speilet fra det migrasjonene
-- faktisk oppretter (003/004 for authenticator, 005/007 for m37_claimer).
-- Alt annet ikke-extension i public eies av migrator. Reparasjonen virker
-- BEGGE veier: et designobjekt hos feil eier flyttes til sin designede
-- eier, et ordinært objekt hos en privilegert rolle flyttes til migrator.
-- Til slutt en SLUTTKONTROLL som feiler hardt hvis noe fortsatt står hos
-- feil eier — en reparasjon som ikke kan bevise sitt eget resultat er en
-- anbefaling.
--
-- Kjøres av postgres (oppsett-postgresql.sh) eller migrator (testene) —
-- begge er medlem av/har SET ROLE til de designede eierne.
-- Rollenavnene er konstanter her som i migrer.py sin M37_RETTIGHETER:
-- de er ikke konfigurasjon, de er arkitektur.
-- ============================================================

-- Egen transaksjon: temp-tabellen lever nøyaktig så lenge reparasjonen —
-- og feiler sluttkontrollen, rulles også reparasjonen tilbake, så en
-- halvreparert base ikke kan bli stående.
BEGIN;

CREATE TEMP TABLE _design (art TEXT, ident TEXT, eier TEXT) ON COMMIT DROP;
INSERT INTO _design VALUES
    -- 003/004: tokenlagring og verifisering eies av authenticator —
    -- runtime når tabellen KUN gjennom SECURITY DEFINER-funksjonen.
    ('TABLE',    'api_tokener',                    'disponit_authenticator'),
    ('FUNCTION', 'verifiser_token(text,text)',     'disponit_authenticator'),
    -- 009: PENDING-verifikasjonen for token-CLI-en (PR-009 V2).
    -- Paritetstesten fanget selv at denne manglet da 009 landet — nøyaktig
    -- jobben dens: en ny privilegert eid funksjon kan ikke bli stille udekket.
    ('FUNCTION', 'hent_pending_token(text)',       'disponit_authenticator'),
    -- 010: herdet sesjonsoppslag (PR-010 §1). Definer-funksjon eid av
    -- authenticator, fanget av paritetstesten som hent_pending_token.
    ('FUNCTION', 'slaa_opp_sesjon(text)',          'disponit_authenticator'),
    -- 005 §6–9 + 007: M-37-flaten eies av NOLOGIN-rollen m37_claimer.
    ('TABLE',    'arbeidskapabiliteter',           'disponit_m37_claimer'),
    ('TABLE',    'kvitteringskapabiliteter',       'disponit_m37_claimer'),
    ('FUNCTION', 'arkiver_policyversjon(text,text,text)',            'disponit_m37_claimer'),
    ('FUNCTION', 'bruk_kapabilitet(text,text)',                      'disponit_m37_claimer'),
    ('FUNCTION', 'bruk_kvitteringskapabilitet(text,text)',           'disponit_m37_claimer'),
    ('FUNCTION', 'claim_neste_oppdrag(text,text[],text,integer,text,text,bigint)', 'disponit_m37_claimer'),
    -- 063 (#165): fornyelsesveien — claim-livssyklussteg, claimers eie.
    ('FUNCTION', 'forny_oppdragslease(bigint,text,text,integer,integer)', 'disponit_m37_claimer'),
    -- 005→015 (Codex P1): den GAMLE 4-args-signaturen står her til den er borte
    -- overalt. Reparasjonen kjører FØR migrer.py (oppsett-postgresql.sh) og som
    -- superbruker: på en base som ennå ikke har kjørt 015 ville steg 2
    -- klassifisert den fortsatt installerte gamle funksjonen som strøgods og
    -- flyttet den til migrator. 015 dropper den under `SET LOCAL ROLE
    -- disponit_m37_claimer` og ville da feilet på manglende eierskap —
    -- medlemskapet er `WITH INHERIT FALSE`, så migrator kan heller ikke droppe
    -- den på claimers vegne, og HELE oppgraderingen fra 005 stopper. Raden er
    -- transitorisk: etter 015 finnes ikke funksjonen, og designrader uten
    -- objekt hoppes stille over (oppslaget er to_regprocedure → NULL).
    ('FUNCTION', 'claim_neste_oppdrag(text,text[],text,integer)',    'disponit_m37_claimer'),
    ('FUNCTION', 'claim_neste_sak(text,integer)',                    'disponit_m37_claimer'),
    -- 038: outbox-skriveveiene + saks- og reaperfunksjonene eies av claimer
    -- (samme rolle som skriver oppdrag/unntak i claim-veien fra foer)
    ('FUNCTION', 'opprett_reparasjonsoppdrag(text,bigint,bigint,text,text,text,text,bytea,text,bytea,timestamp with time zone,timestamp with time zone,bigint,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'opprett_beslutningsoppdrag(text,bigint,text,text,text,bytea,text,bytea,timestamp with time zone,timestamp with time zone)', 'disponit_m37_claimer'),
    -- 056: M-57-utsendingskjedens funksjoner — samme eier som outbox-
    -- familiens opphavsveier. Paritetstesten fanget at de manglet her —
    -- nøyaktig jobben dens.
    ('FUNCTION', 'opprett_utsendingsliste(text,uuid,uuid,bigint,text,text,uuid[],uuid,integer)', 'disponit_m37_claimer'),
    -- 081: senderbenens dører.
    ('FUNCTION', 'm57_neste_sendinger(text,integer,integer)', 'disponit_m37_claimer'),
    ('FUNCTION', 'm57_start_sending(text,uuid,uuid,uuid,integer)', 'disponit_m37_claimer'),
    ('FUNCTION', 'm57_fullfor_sending(text,uuid,uuid,uuid,text,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'm57_sendeklare_tenanter(integer,integer)', 'disponit_domene_eier'),
    ('FUNCTION', 'm57_merk_uviss(interval)', 'disponit_domene_eier'),
    -- 056-signaturen står til 080 har droppet den (CodeRabbit critical):
    -- kjører reparasjonen på en base FØR 080, ville en manglende rad
    -- flyttet den claimer-eide døren til migrator — og 080s DROP (som
    -- claimer) dødd på eierskap. (Merk: aldri semikolon i kommentarene
    -- her — VALUES-blokken parses på setningsskilletegnet.)
    ('FUNCTION', 'opprett_utsendingsliste(text,uuid,uuid,bigint,text,text,text,integer)', 'disponit_m37_claimer'),
    ('FUNCTION', 'signer_utsendingsliste(text,uuid,text,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'frigi_utsendelse(text,uuid,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'opprett_frigivelsesoppdrag(text,uuid,text,text,text,bytea,text,bytea,timestamp with time zone,timestamp with time zone)', 'disponit_m37_claimer'),
    ('FUNCTION', 'sikre_sak_for_oppdrag(text,bigint,text,text,text)', 'disponit_m37_claimer'),
    -- 066 (#159): revisjonshendelsens skrive- og lesevei. Samme eier og
    -- samme grunn som 057-doerene over — de gaar gjennom
    -- krev_tenantkontekst-porten og maa derfor lages i porteierens
    -- SET ROLE-vindu. Tabellen selv eies av migrator og staar ikke her.
    ('FUNCTION', 'skriv_revisjonshendelse(text,text,text,text,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'les_revisjonshendelse(text,uuid)',                  'disponit_m37_claimer'),
    -- 043 (Gate 14b): oppløsningsveien — kansellering med fencing.
    ('FUNCTION', 'bruk_kvitteringskapabilitet(text,text,text)',        'disponit_m37_claimer'),
    ('FUNCTION', 'avvis_med_opplosning(text,bigint,bigint[],text,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'reversibilitet_for_oppdrag(text,bigint)',            'disponit_m37_claimer'),
    ('FUNCTION', 'reap_evidensfrister(integer)',                      'disponit_m37_claimer'),
    -- 057: kandidatprosessens funksjoner — eies av claimer fordi de gaar
    -- gjennom krev_tenantkontekst-porten (samme vindu, samme eier), og
    -- reaperen er kryss-tenant paa 038-formen.
    ('FUNCTION', 'opprett_rekrutteringsprosess(text,bigint,integer)', 'disponit_m37_claimer'),
    -- 075 (#157): kandidatankerets fødselsdør — samme eier, samme grunn.
    ('FUNCTION', 'opprett_kandidat(text,uuid,uuid)', 'disponit_m37_claimer'),
    -- 076 (#163): markøren som armerer samlet-porten — claimer-eid
    -- definer av samme grunn som lagervaktene.
    ('FUNCTION', 'm57_marker_beroert_prosess()', 'disponit_m37_claimer'),
    -- 082 (M-8): kunde-/utstederdørene gaar gjennom
    -- krev_tenantkontekst-porten — samme vindu, samme eier som
    -- 057-doerene.
    ('FUNCTION', 'm8_opprett_slot(text,uuid,timestamp with time zone,timestamp with time zone,integer,uuid)', 'disponit_m37_claimer'),
    ('FUNCTION', 'm8_deaktiver_slot(text,uuid)', 'disponit_m37_claimer'),
    ('FUNCTION', 'm8_utsted_tidsvalgtoken(text,uuid,uuid,text,text,integer)', 'disponit_m37_claimer'),
    -- 089 (M-35): kontinuitetsdoerene gaar gjennom
    -- krev_tenantkontekst-porten — samme vindu, samme eier som
    -- 057/082-doerene.
    ('FUNCTION', 'm35_opprett_tjeneste(text,text,text,text,integer,integer,text,text,text,uuid)', 'disponit_m37_claimer'),
    ('FUNCTION', 'm35_oppdater_tjeneste(text,uuid,text,integer,integer,text,text,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'm35_opprett_kontakt(text,text,smallint,text,text,uuid)', 'disponit_m37_claimer'),
    ('FUNCTION', 'm35_bekreft_kontakt(text,uuid,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'm35_opprett_hendelse(text,text,jsonb,text,text,uuid)', 'disponit_m37_claimer'),
    ('FUNCTION', 'm35_legg_post(text,uuid,text,text,text,uuid)', 'disponit_m37_claimer'),
    ('FUNCTION', 'm35_lukk_hendelse(text,uuid,text,text)', 'disponit_m37_claimer'),
    -- 082 (M-8): kapabilitetstabellen og de to offentlige doerene eies
    -- av authenticator (004-presedensen — konstanttiden bor i defineren,
    -- og oppslaget paa token_id alene gaar gjennom eierpolicyen).
    ('TABLE',    'm8_tidsvalgtoken', 'disponit_authenticator'),
    ('FUNCTION', 'm8_tidsvalg_oppslag(text,text)', 'disponit_authenticator'),
    ('FUNCTION', 'm8_velg_slot(text,text,uuid)', 'disponit_authenticator'),
    -- 078 (#156): pseudonymfunksjonen — nøkkelen forlater aldri basen.
    ('FUNCTION', 'm57_pseudonym(text,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'lukk_rekrutteringsprosess(text,uuid,timestamp with time zone)', 'disponit_m37_claimer'),
    ('FUNCTION', 'reap_kandidatdata(integer)',                        'disponit_m37_claimer'),
    -- 069: tidligslettingsdoeren gaar gjennom samme
    -- krev_tenantkontekst-port og lukker via lukk_rekrutteringsprosess
    -- — samme vindu, samme eier som 057-funksjonene over.
    ('FUNCTION', 'bestill_tidligsletting(text,uuid)',                 'disponit_m37_claimer'),
    -- 088 (M-6): e-postreaperen er kryss-tenant paa 038/057-formen og
    -- den utsatte samlet-porten kjoerer ved COMMIT, etter at definer-
    -- identiteten er borte -- begge maa lese gjennom claimerens
    -- m6_reaper-policy, saa eierskapet ER lesetilgangen (057-radenes
    -- egen begrunnelse, ordrett)
    ('FUNCTION', 'reap_epostdata(integer)',                           'disponit_m37_claimer'),
    ('FUNCTION', 'm6_lagrene_reapes_samlet()',                        'disponit_m37_claimer'),
    -- 088 (M-6): markoeren som armerer samlet-porten -- claimer-eid
    -- definer av samme grunn som 076s m57_marker_beroert_prosess
    ('FUNCTION', 'm6_marker_beroert_melding()',                       'disponit_m37_claimer'),
    -- 090 (M-10) / 091 (M-11): driftstatusens seks doerer. Alle seks
    -- opprettes i det samme SET ROLE-vinduet som 051/057-doerene, og av
    -- samme grunn: lesedoerene gaar gjennom krev_tenantkontekst-porten,
    -- som er claimer-eid, og en definer som skal PASSERE den porten maa
    -- ha samme eier. Skrivedoerene og sveipene deler vindu fordi de
    -- deler tabell -- eierskapet ER skrivetilgangen for dem (057-radenes
    -- egen begrunnelse, ordrett)
    ('FUNCTION', 'registrer_backupverifisering(timestamp with time zone,timestamp with time zone,numeric,integer,bigint)', 'disponit_m37_claimer'),
    ('FUNCTION', 'backup_status(text,integer)',                       'disponit_m37_claimer'),
    ('FUNCTION', 'varsle_backupverifisering_uteblitt(text)',          'disponit_m37_claimer'),
    ('FUNCTION', 'registrer_selvtest(uuid,jsonb,text)',               'disponit_m37_claimer'),
    ('FUNCTION', 'selvtest_status(text,integer)',                     'disponit_m37_claimer'),
    ('FUNCTION', 'varsle_selvtest_uteblitt(text)',                    'disponit_m37_claimer'),
    -- 057 port 19: den UTSATTE porten er claimer-eid definer, ikke en vakt
    -- som migrator. Den kjoerer ved COMMIT, etter at reaperens definer-
    -- identitet er borte, og maa lese gjennom claimerens m57_reaper-policy
    -- uansett hvem som committer. Eierskapet ER lesetilgangen her.
    ('FUNCTION', 'm57_lagrene_reapes_samlet()',                       'disponit_m37_claimer'),
    -- 038 §4-porten (Codex P1): binder `p_tenant` til kallerens
    -- tenantkontekst i definer-veiene over. Opprettes i det samme
    -- SET ROLE-vinduet og hoerer derfor til den samme eieren.
    ('FUNCTION', 'krev_tenantkontekst(text,text)',                    'disponit_m37_claimer'),
    -- 058: inndata-doerene (#162) — samme eier som artefakt-funksjonene
    -- i 016/017 (domene_eier), samme grunn: kapabilitetsformen.
    -- IDENTITETEN ER SIGNATUREN (Codex P1): raden her matcher paa
    -- navn+argumenttyper, ikke paa navn. `reserver_inndata` fikk
    -- `p_idempotensnokkel` som femte argument i denne PR-en, og en
    -- fire-argumentet rad ville derfor ikke funnet den ekte funksjonen.
    -- Det er ikke en tom rad: steg 2 klassifiserer alt SECURITY DEFINER
    -- som IKKE staar her som udesignet og gir det til `disponit_migrator`
    -- — foerste rerun av `oppsett-postgresql.sh` etter at 058 er kjoert
    -- ville altsaa flyttet doeren bort fra `disponit_domene_eier`, mens
    -- 058 hoppes over som allerede anvendt. Migratoren har `WITH INHERIT
    -- FALSE`, saa `krev_tenantkontekst` (grantet til domene_eier) ville
    -- feilet med `permission denied` paa hver eneste reservasjon.
    ('FUNCTION', 'reserver_inndata(text,text,text,bigint,text)', 'disponit_domene_eier'),
    -- 059 (B-maskinen, #192): `registrer_inndata_lastet` MISTET
    -- `p_lager_sti` — stien foedes av doeren selv. Identiteten er
    -- signaturen, saa raden foelger den nye formen. En rad paa den gamle
    -- sju-argumenters formen hadde vaert toemt for mening: funksjonen
    -- finnes ikke, og den EKTE doeren ville blitt klassifisert som
    -- udesignet og flyttet til migratoren ved neste rerun.
    ('FUNCTION', 'registrer_inndata_lastet(text,text,bigint,text,text,bytea)', 'disponit_domene_eier'),
    -- 058->059 transitorisk (samme moenster som 015/052-radene): kjoerer
    -- reparasjonen paa en base som ennaa staar paa 058, er det den GAMLE
    -- sju-argumenters formen som finnes. Uten raden her ville steg 2
    -- klassifisert den som udesignet og gitt den til migratoren — og 059,
    -- som DROPper den under SET LOCAL ROLE disponit_domene_eier, ville
    -- feilet paa eierskap. Etter 059 finnes ikke formen, og designrader
    -- uten objekt hoppes stille over.
    ('FUNCTION', 'registrer_inndata_lastet(text,text,bigint,text,text,bytea,text)', 'disponit_domene_eier'),
    ('FUNCTION', 'bind_inndata(text,uuid,bigint,text)',     'disponit_domene_eier'),
    -- 061 (#189): stillingsprofilens produsent — samme eier.
    ('FUNCTION', 'opprett_stillingsprofil_versjon(text,uuid,text,text,jsonb,text)', 'disponit_domene_eier'),
    -- 079 (#160): utsendingstekstens dører — samme familie, samme eier.
    ('FUNCTION', 'opprett_utsendingstekst_versjon(text,uuid,text,text,text,text)', 'disponit_domene_eier'),
    ('FUNCTION', 'skjul_utsendingstekst(text,uuid)', 'disponit_domene_eier'),
    -- 060 (#162 PR-2, B-formen fra #200): modulens lesevei — samme eier
    -- som de andre inndata-doerene.
    ('FUNCTION', 'hent_inndata_for_oppdrag(bigint,text,text,text,text)', 'disponit_domene_eier'),
    -- 044: periodisk kontroll — planens herdede funksjoner eies av claimer
    -- (tabellene er migrator-eide med eksplisitte grants — runtime når dem
    -- KUN gjennom funksjonene, og CURRENT_USER-policyen ser på tvers).
    -- MERK: aldri semikolon i kommentarene her — parseren deler på det.
    ('FUNCTION', 'opprett_plan(text,text,jsonb,text,smallint,smallint,smallint,text,text,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'aktiver_plan(text,uuid,text,text)',                 'disponit_m37_claimer'),
    ('FUNCTION', 'pause_plan(text,uuid,text,text,text,jsonb)',        'disponit_m37_claimer'),
    ('FUNCTION', 'gjenoppta_plan(text,uuid,text,text)',               'disponit_m37_claimer'),
    ('FUNCTION', 'stans_plan(text,uuid,text,text)',                   'disponit_m37_claimer'),
    ('FUNCTION', 'plan_forfallsminutt(uuid)',                         'disponit_m37_claimer'),
    ('FUNCTION', 'plan_vindu_idempotensnokkel(uuid,timestamp with time zone)', 'disponit_m37_claimer'),
    ('FUNCTION', 'tick_i_apen_periode(uuid,timestamp with time zone)', 'disponit_m37_claimer'),
    ('FUNCTION', 'forfalte_planvinduer(integer)',                     'disponit_m37_claimer'),
    ('FUNCTION', 'utlopte_planvinduer(integer,integer)',              'disponit_m37_claimer'),
    ('FUNCTION', 'claim_planvindu(text,uuid,timestamp with time zone,integer)', 'disponit_m37_claimer'),
    ('FUNCTION', 'frigi_planvindu(text,uuid,timestamp with time zone,uuid)', 'disponit_m37_claimer'),
    ('FUNCTION', 'terminaliser_planvindu(text,uuid,timestamp with time zone,uuid,text,text,bigint,jsonb)', 'disponit_m37_claimer'),
    ('FUNCTION', 'plan_nedetid_aggregert(text,uuid,timestamp with time zone,timestamp with time zone,integer,text,text,boolean)', 'disponit_m37_claimer'),
    ('FUNCTION', 'hent_planer(text)',                                 'disponit_m37_claimer'),
    ('FUNCTION', 'hent_plan_tick(text,uuid,integer)',                 'disponit_m37_claimer'),
    ('FUNCTION', 'm16_beslutninger(text,timestamp with time zone,timestamp with time zone)', 'disponit_m37_claimer'),
    -- Fase 2 (084): m16_frekvens ERSTATTET m16_frekvensreservasjoner og
    -- m16_tick_alltid kom til. Skalarraden STÅR fordi den fortsatt
    -- finnes i enhver base på 051–083: reparasjonen kan kjøres der FØR
    -- 084 migreres, og uten raden ville den flatet skalaren til
    -- migrator — hvorpå 084s ryddeblokk (som claimer) ikke lenger eier
    -- den og hele migrasjonen stopper. Etter 084 finnes ikke funksjonen
    -- og raden er inert (design uten objekt hoppes stille over) — den
    -- kan fjernes når 084 er kjørt i alle baser.
    ('FUNCTION', 'm16_frekvensreservasjoner(text,timestamp with time zone,timestamp with time zone)', 'disponit_m37_claimer'),
    ('FUNCTION', 'm16_frekvens(text,timestamp with time zone,timestamp with time zone)', 'disponit_m37_claimer'),
    ('FUNCTION', 'm16_tick_alltid(text)',                             'disponit_m37_claimer'),
    ('FUNCTION', 'm16_aktiveringer(text,timestamp with time zone,timestamp with time zone)', 'disponit_m37_claimer'),
    ('FUNCTION', 'm16_oppdrag(text,timestamp with time zone,timestamp with time zone)', 'disponit_m37_claimer'),
    -- Haleargumentet `p_sakstyper` er sakstypevernet (`security:read`) —
    -- settet er kallerens, som `p_terminale`. 051 har aldri vært
    -- deployet, så de tidligere formene finnes ikke i noen base og skal
    -- ikke stå her.
    ('FUNCTION', 'm16_unntak_aktivitet(text,timestamp with time zone,timestamp with time zone,text[])', 'disponit_m37_claimer'),
    ('FUNCTION', 'm16_unntak_lukkede(text,timestamp with time zone,timestamp with time zone,text[],text[],integer)', 'disponit_m37_claimer'),
    ('FUNCTION', 'm16_unntak_apne(text,text[],text[])',               'disponit_m37_claimer'),
    ('FUNCTION', 'm16_tick(text,timestamp with time zone,timestamp with time zone)', 'disponit_m37_claimer'),
    ('FUNCTION', 'hent_plan_hendelser(text,uuid,integer)',            'disponit_m37_claimer'),
    ('FUNCTION', 'planer_med_menneskelig_avvis()',                    'disponit_m37_claimer'),
    ('FUNCTION', 'planer_gjentatt_uten_resultat()',                   'disponit_m37_claimer'),
    ('FUNCTION', 'planer_med_ubehandlet_stopp()',                     'disponit_m37_claimer'),
    ('FUNCTION', 'pause_gjentatt_uten_resultat(text,uuid,text,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'planer_med_gjentatt_brudd()',                       'disponit_m37_claimer'),
    ('FUNCTION', 'plan_bestillingstyper()',                           'disponit_m37_claimer'),
    ('FUNCTION', 'planvinduer_til_klassifisering(integer,integer)',   'disponit_m37_claimer'),
    ('FUNCTION', 'plan_nedetid_kandidater(integer,integer)',          'disponit_m37_claimer'),
    ('FUNCTION', 'varsle_plan_brudd(text,uuid,text,text)',            'disponit_m37_claimer'),
    ('FUNCTION', 'forny_claim(text,bigint,text,integer,integer)',    'disponit_m37_claimer'),
    ('FUNCTION', 'frigi_hengende_kapabiliteter()',                   'disponit_m37_claimer'),
    ('FUNCTION', 'frigi_utlopte_claims()',                           'disponit_m37_claimer'),
    ('FUNCTION', 'innlos_kvitteringskapabilitet(text,text)',         'disponit_m37_claimer'),
    -- 035 la til haleargumentene for DEPLOYMENTEN i begge kvitterings-
    -- funksjonene, som for artefaktkapabiliteten lenger nede. BEGGE formene
    -- står her: reparasjonen kjører FØR migrer.py, så en base som ennå ikke
    -- har kjørt 035 har de gamle signaturene installert og eid av
    -- m37_claimer. Sto de ikke her, ville steg 2 flyttet dem til migrator —
    -- og 035, som dropper dem under SET LOCAL ROLE disponit_m37_claimer,
    -- ville feilet på eierskap.
    ('FUNCTION', 'innlos_kvitteringskapabilitet(text,text,text,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'kvitteringskapabilitet_deployment_frosset()',       'disponit_m37_claimer'),
    ('FUNCTION', 'kapabilitet_innenfor_claim()',                     'disponit_m37_claimer'),
    ('FUNCTION', 'kapabilitet_statusmaskin()',                       'disponit_m37_claimer'),
    ('FUNCTION', 'knytt_verifikasjonsoppdrag(text,bigint,text,integer,bigint)', 'disponit_m37_claimer'),
    ('FUNCTION', 'kvitteringskapabilitet_statusmaskin()',            'disponit_m37_claimer'),
    ('FUNCTION', 'registrer_verifikasjonsbevis(bigint,text,text,text,text,text,jsonb,text,integer,integer,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'reserver_kapabilitet(text,text,integer)',          'disponit_m37_claimer'),
    -- 011 (PR-012 gate 14a): lesevei for utestående oppdrag/kapabilitet.
    -- Eid av m37_claimer fordi arbeidskapabiliteter er off-limits for runtime
    -- (runtime får KUN EXECUTE). Paritetstesten fanget denne da 14a landet.
    ('FUNCTION', 'sak_utestaaende(text,bigint)',                     'disponit_m37_claimer'),
    ('FUNCTION', 'start_verifikasjonsgenerasjon(text,bigint,text,integer,jsonb,text,text,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'tenanter_uten_policysnapshot()',                   'disponit_m37_claimer'),
    ('FUNCTION', 'utsted_arbeidskapabilitet(text,integer,text,integer)', 'disponit_m37_claimer'),
    ('FUNCTION', 'utsted_kvitteringskapabilitet(bigint,text,integer,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'utsted_kvitteringskapabilitet(bigint,text,integer,text,text,text)', 'disponit_m37_claimer'),
    -- 013 (PR-013): den herdede aktiveringsfunksjonen. Eid av
    -- disponit_policy_eier fordi policyer/policy_hode er off-limits for runtime
    -- (runtime får KUN EXECUTE). Ny privilegert eier — paritetstesten dekker den.
    ('FUNCTION', 'aktiver_policy(text,text,integer,text)',           'disponit_policy_eier'),
    -- 032: signaturen bærer den forventede aktive versjonen + innholdshashen
    -- (den optimistiske låsen). Toargumentsformen finnes ikke lenger — den
    -- slettet uten å binde seg til en versjon, og migrasjonen dropper den.
    ('FUNCTION', 'slett_ubrukt_policy(text,text,text,text)', 'disponit_policy_eier'),
    -- 047: historikk-leseveiene (flaten leser aldri policyer direkte).
    ('FUNCTION', 'policyversjoner_for_tenant(text,text)',   'disponit_policy_eier'),
    -- Generasjonen står i signaturen: et versjonsnummer er en peker som
    -- `slett_ubrukt_policy` frigjør, så diffen må navngi identiteten.
    ('FUNCTION', 'policyversjon_innhold(text,text,text,bigint)', 'disponit_policy_eier'),
    -- Kilden for en rullbakk: innholdet OG generasjonens egen
    -- `innholds_hash` i ett oppslag — opphavet lagres som identiteten,
    -- ikke som versjonsnummeret, og de to må komme fra samme rad.
    ('FUNCTION', 'policyversjon_kilde(text,text,text)',     'disponit_policy_eier'),
    -- 048 (R47-1): kvorumsgaten ved hendelses-etablering (SP-9s andre
    -- form — trigger ved etablering pluss immutabilitet).
    ('FUNCTION', 'hendelse_kvorum_gate()',                    'disponit_policy_eier'),
    -- 047: vakten «én hendelse per LEVENDE versjon». Den er SECURITY
    -- DEFINER og eid av policy_eier fordi en DEFERRED constraint-trigger
    -- fyrer ved COMMIT, utenfor `aktiver_policy` sin definer-kontekst —
    -- altså som runtime-rollen, som verken har SELECT på `policyaktivering`
    -- eller eierens RLS-policy.
    ('FUNCTION', 'hendelse_en_per_levende_versjon()',       'disponit_policy_eier'),
    -- 047: prøven «har denne versjonen vært i kraft». Ren funksjon av fire
    -- skalarer, så EXECUTE står til PUBLIC som normalt — men EIEREN er
    -- policy_eier, som for de andre 047-definerne, og da må den stå her.
    -- Uten raden klassifiserer reparasjonen den som strøgods og flytter
    -- eierskapet til migrator, og definerne som kaller den er ikke lenger
    -- det designet sier de er.
    ('FUNCTION', 'policyversjon_i_kraft(boolean,timestamp with time zone,text,timestamp with time zone)', 'disponit_policy_eier'),
    -- 014 (PR-014a): modulregisterets herdede overgangsfunksjoner. Eid av
    -- disponit_modul_eier fordi registertabellene er off-limits for runtime
    -- (runtime får KUN SELECT). Paritetstesten dekker dem.
    ('FUNCTION', 'installer_modul(text,text)',                        'disponit_modul_eier'),
    -- 035: modul-onboarding — utstedelse/innløsning/rotasjon/tilbakekalling
    -- eies av modul_eier — runtime når tabellene KUN gjennom funksjonene
    -- (verifiser_modultoken er den eneste leseveien).
    ('FUNCTION', 'utsted_onboarding_hemmelighet(text,text,text,uuid,text,integer,integer,text)', 'disponit_modul_eier'),
    ('FUNCTION', 'innlos_onboarding(uuid,text,uuid,text,integer,text)', 'disponit_modul_eier'),
    -- 035 la til innløsningens idempotensnøkkel som haleargument. BEGGE
    -- formene står her, av samme grunn som for de andre utvidede
    -- signaturene: reparasjonen kjører FØR migrer.py.
    ('FUNCTION', 'innlos_onboarding(uuid,text,uuid,text,integer,text,uuid)', 'disponit_modul_eier'),
    ('FUNCTION', 'verifiser_modultoken(text)',                         'disponit_modul_eier'),
    ('FUNCTION', 'modultoken_fortsatt_autorisert(uuid,text,text,text,bigint)', 'disponit_modul_eier'),
    -- Siste ledd er rotasjonens idempotensnøkkel (035, Codex P1) — den gamle
    -- femargumentsformen finnes ikke lenger, migrasjonen dropper den.
    -- INGEN SEMIKOLON I KOMMENTARENE HER: både reparasjonskjøringen og
    -- paritetstesten deler filen på setningsskilletegnet, så et semikolon i
    -- prosa kutter designtabellen på midten og lar resten av radene
    -- forsvinne stille.
    ('FUNCTION', 'roter_modultoken(uuid,uuid,text,integer,text,uuid)', 'disponit_modul_eier'),
    ('FUNCTION', 'tilbakekall_modultoken(uuid,text,text)',             'disponit_modul_eier'),
    ('FUNCTION', 'varsle_tokenfamilie_utlop(text)',                    'disponit_modul_eier'),
    -- 036 (PR-014c): skjemalageret og målautorisasjonsregisteret.
    ('FUNCTION', 'registrer_artefaktskjema(text,text,text)',           'disponit_modul_eier'),
    -- Metavakten registreringen kaller. Den er ren (IMMUTABLE, leser ingen
    -- tabeller), men eies av samme rolle som kalleren — en SECURITY
    -- DEFINER-funksjon skal ikke kunne omdefineres av noen andre enn den
    -- som eier veien den står i.
    ('FUNCTION', '_artefaktskjema_typefeil(jsonb)',                    'disponit_modul_eier'),
    ('FUNCTION', 'registrer_malautorisasjonsvilkar(text,text,text)',   'disponit_modul_eier'),
    ('FUNCTION', 'registrer_oppdragstype(text,text,integer,text,text)', 'disponit_modul_eier'),
    ('FUNCTION', 'sett_modulstatus(text,text,text,text)',             'disponit_modul_eier'),
    ('FUNCTION', 'registrer_kontrakt(text,integer,text,text,text,text,text,text)', 'disponit_modul_eier'),
    ('FUNCTION', 'registrer_release(text,text,integer,text,text,text,text)', 'disponit_modul_eier'),
    ('FUNCTION', 'registrer_moduldrill(text,text,text,text,text,text,bigint,bigint,bigint,bigint,text,text,text,timestamp with time zone)', 'disponit_modul_eier'),
    ('FUNCTION', 'aksepter_moduldeployment(text,text,text,bigint,text,text,uuid,text,text,text,text,jsonb,text,text)', 'disponit_modul_eier'),
    ('FUNCTION', 'aksepter_moduldeployment(text,text,text,bigint,text,text,uuid,text,text,text,text,jsonb,text,text,bigint[])', 'disponit_modul_eier'),
    ('FUNCTION', 'attester_ci_kjoring(text,text,text,text,text,text,text)', 'disponit_modul_eier'),
    -- 052: attesten navngir også drillartefaktet den ble regnet mot
    -- (Codex P1, PR #123) — signaturen er den nye, seksargs. BEGGE formene
    -- står her, av samme grunn som for claim-signaturen over: reparasjonen
    -- kjører FØR migrer.py, så en base som ennå ikke har kjørt 052 har den
    -- gamle femargumentsformen installert og eid av modul_eier. Sto den
    -- ikke her, ville steg 2 klassifisert den som strøgods og flyttet den
    -- til migrator — og 052, som dropper den under SET LOCAL ROLE
    -- disponit_modul_eier, ville feilet på manglende eierskap. Raden er
    -- transitorisk: etter 052 finnes ikke femargumentsformen, og
    -- designrader uten objekt hoppes stille over.
    ('FUNCTION', 'attester_evidensfil(text,text,text,jsonb,text)',    'disponit_modul_eier'),
    ('FUNCTION', 'attester_evidensfil(text,text,text,jsonb,text,text)', 'disponit_modul_eier'),
    ('FUNCTION', 'attester_evidensfil(text,text,text,jsonb,text,text,text)', 'disponit_modul_eier'),
    ('FUNCTION', 'maal_rent_utfall(text,bigint)',                     'disponit_modul_eier'),
    -- 053: signaturbyttene. De gamle formene står TRANSITORISK (#123
    -- r3-fella: reparasjonen kjører FØR migrer.py på en base som ennå
    -- står på 052, og steg 2 gir strøgods til migrator — som ikke kan
    -- droppe på eierens vegne). Designrader uten objekt hoppes stille
    -- over etter 053.
    ('FUNCTION', 'maal_kjoringsattest(text,bigint,text,text)',        'disponit_modul_eier'),
    ('FUNCTION', 'maal_kjoringsattest(text,bigint,text,text,text,text)', 'disponit_modul_eier'),
    ('FUNCTION', 'aksepter_plattformmodul(text,text,text,text,text,text,jsonb,text,text)', 'disponit_modul_eier'),
    -- 054: kompletthetsvakten på plattformaksepten. Den er SECURITY
    -- DEFINER og MÅ eies av modul_eier — den fyrer ved COMMIT, utenfor
    -- akseptfunksjonens definer-kontekst, og eieren er den eneste rollen
    -- som ser både punktbordet og kravpunktregisteret. Flatet ut til
    -- migrator ville den lest med for brede rettigheter, og derfor står
    -- den i designet, ikke i strøgodset.
    ('FUNCTION', 'plattformaksept_er_komplett()',                     'disponit_modul_eier'),
    ('FUNCTION', 'bytt_release(text,text,text,integer,text,text)',    'disponit_modul_eier'),
    ('FUNCTION', 'pensjoner_release(text,text,text,text)',            'disponit_modul_eier'),
    ('FUNCTION', 'noddeaktiver_modul(text,text,text)',               'disponit_modul_eier'),
    ('FUNCTION', 'reaktiver_modul(text,bigint,text)',                'disponit_modul_eier'),
    -- 086 (M-31): modellstyringsdørene — golden-sett-lageret, kravsettet
    -- og kjøringsregistreringen eies av modul_eier som resten av
    -- registerdørene (runtime har KUN SELECT på tabellene, all skriving
    -- går gjennom definerne). bytt_release-raden over dekker også
    -- 086-REPLACEen (samme signatur, samme eier)
    ('FUNCTION', 'registrer_golden_sett(text,text,integer,text,integer,text,text)', 'disponit_modul_eier'),
    ('FUNCTION', 'sett_evalueringskrav(text,text,integer,text,numeric,integer,integer,text)', 'disponit_modul_eier'),
    ('FUNCTION', 'registrer_evalueringskjoring(text,uuid,text,text,integer,text,integer,integer,integer,integer,integer,numeric,text,text,timestamp with time zone,timestamp with time zone,text)', 'disponit_modul_eier'),
    ('FUNCTION', 'ta_deployreservasjon(text,text,text,text,interval)', 'disponit_modul_eier'),
    ('FUNCTION', 'forleng_deployreservasjon(text,text,text,interval)', 'disponit_modul_eier'),
    ('FUNCTION', 'frigi_deployreservasjon(text,text,text)',          'disponit_modul_eier'),
    -- PR-014b: domene/artefakt-funksjonene, eid av disponit_domene_eier.
    ('FUNCTION', 'utsted_challenge(text,text,boolean,text,text)',     'disponit_domene_eier'),
    ('FUNCTION', 'verifiser_domenekontroll(text,text,boolean,text)',  'disponit_domene_eier'),
    ('FUNCTION', 'revalider_domenekontroll(text,text,text)',          'disponit_domene_eier'),
    ('FUNCTION', 'tilbakekall_domenekontroll(text,text,text,text)',   'disponit_domene_eier'),
    ('FUNCTION', 'avgjor_domeneovertakelse(text,text,bigint,boolean,text)', 'disponit_domene_eier'),
    -- 027: varselsenderens kryss-tenant-vindu. Samme eier og samme grunn —
    -- én rolle for «funksjoner som med vilje ser på tvers av tenanter».
    ('FUNCTION', 'varsel_klaim_epost(integer,integer)',              'disponit_domene_eier'),
    ('FUNCTION', 'varsel_sett_epoststatus(bigint,uuid,text,text)',        'disponit_domene_eier'),
    ('FUNCTION', 'varsel_rekoe(interval,integer,interval)',          'disponit_domene_eier'),
    ('FUNCTION', 'registrer_artefakttype(text,text,integer,text,text,text)', 'disponit_domene_eier'),
    ('FUNCTION', 'lagre_artefakt_staged(text,bigint,text,text,text,integer,text,bigint,integer,text,bytea,bytea,text,text)', 'disponit_domene_eier'),
    ('FUNCTION', 'promoter_artefakt(uuid,text,bigint,text,bigint,text,text)', 'disponit_domene_eier'),
    ('FUNCTION', 'rydd_staged_artefakter()',                          'disponit_domene_eier'),
    ('FUNCTION', 'karantenesett_artefakt(uuid,text,bigint)',         'disponit_domene_eier'),
    ('FUNCTION', 'bevar_artefakt(uuid,text,bigint,text)',                 'disponit_domene_eier'),
    -- 066/#222: makuleringsdøren. Samme eier som resten av 016-familien,
    -- av samme grunn — den skriver på `artefakt`, og reaperen får EXECUTE
    -- på DØREN i stedet for UPDATE på evidenstabellen. `timestamptz`
    -- skrives ut slik `regprocedure` gjengir den, ellers matcher raden
    -- ingen funksjon.
    ('FUNCTION', 'makuler_artefakter_for_prosess(text,bigint,timestamp with time zone)', 'disponit_domene_eier'),
    -- 072 (BESLUTNING-168): versjonsdøren — samme eier som resten av
    -- artefakt-familien, av samme grunn.
    ('FUNCTION', 'registrer_artefaktskjemaversjon(text,integer,text,boolean,text)', 'disponit_domene_eier'),
    -- 072: fødselstriggeren på registeret — SECURITY DEFINER fordi
    -- registrering skjer som modul-/policy-eier, mens raden i
    -- artefakttype_versjon bare kan skrives av domene-eieren.
    ('FUNCTION', 'artefakttype_versjon_foedsel()', 'disponit_domene_eier'),
    -- 043 §8: samme validering og samme lås som `bevar_artefakt`, uten
    -- skrivingen — den sene kvitteringsveien må kunne avvise et fremmed
    -- artefakt også når `direkte`-reversibiliteten sier at artefaktet skal
    -- ryddes, ikke bevares. Samme eier, av samme grunn: låsen på `artefakt`
    -- kan ikke tas av runtime (kun SELECT).
    ('FUNCTION', 'verifiser_artefaktbinding(uuid,text,bigint,text)',      'disponit_domene_eier'),
    -- PR-014b CP5: artefakt-opplastingskapabilitet. Den frittstående brenneren
    -- `bruk_artefaktkapabilitet` er fjernet (forbruk skjer i staged-writen).
    ('FUNCTION', 'utsted_artefaktkapabilitet(text,bigint,text,text,integer,text,bigint,text,text,integer)', 'disponit_domene_eier'),
    ('FUNCTION', 'innlos_artefaktkapabilitet(text,text)',            'disponit_domene_eier'),
    -- 035 la til haleargumentet for MILJØ i begge (kapabiliteten bindes til
    -- hele den autentiserte deploymenten, ikke bare modulen). BEGGE formene
    -- står her, som for den gamle claim-signaturen: reparasjonen kjører FØR
    -- migrer.py, så en base som ennå ikke har kjørt 035 har de gamle
    -- signaturene installert og eid av domene_eier. Sto de ikke her, ville
    -- steg 2 flyttet dem til migrator — og 035, som dropper dem under
    -- SET LOCAL ROLE disponit_domene_eier, ville feilet på eierskap.
    ('FUNCTION', 'utsted_artefaktkapabilitet(text,bigint,text,text,integer,text,bigint,text,text,integer,text)', 'disponit_domene_eier'),
    ('FUNCTION', 'innlos_artefaktkapabilitet(text,text,text,text)',  'disponit_domene_eier'),
    -- PR-015: fire øyne + de bundne driftsformene (migrasjon 019). Samme eier
    -- som resten av domenelaget — avgjørelsen er iboende kryss-tenant, og
    -- `rydd_staged_artefakter(integer)` er 016-regelen med en bunn, ikke en ny
    -- regel, så den hører hjemme hos samme rolle som 0-argumentsformen.
    -- 8. argument (`p_bruker_id`) kom med reautoriseringen av tellende
    -- stemmer, og 9. (`p_forventet_saksrevisjon`) med bindingen til
    -- revisjonen attestanten SÅ (041 §21). INGEN semikolon i denne
    -- kommentaren: både reparasjonen og porten deler filen på
    -- setningsskilletegnet, så ett her ville kuttet VALUES-listen midt av.
    -- Signaturen her MÅ følge den GJELDENDE
    -- migrasjonen, ellers finner reparasjonsløkka ingen funksjon å eie og
    -- den reelle funksjonen blir behandlet som en vanlig, eierløs funksjon
    -- ved neste kjøring. 041 DROPPER åtte-arg utgaven, så den skal heller
    -- ikke stå igjen her: en rad for en funksjon som ikke finnes, er en
    -- rad reparasjonen aldri kan oppfylle.
    ('FUNCTION', 'avgi_overtakelse_attestasjon(text,bigint,text,text,text,text,bigint,text,bigint)', 'disponit_domene_eier'),
    -- `lukk_overtakelsessak` kalles NESTET fra avgi_overtakelse_attestasjon og
    -- må derfor ha samme eier: utelatt herfra ville reparasjonssløyfa nedenfor
    -- behandlet den som en vanlig funksjon og flyttet den til
    -- disponit_migrator ved andre kjøring av `oppsett-postgresql.sh`. 019
    -- hoppes da over på sjekksum, og siden EXECUTE er revoket fra PUBLIC uten
    -- en grant tilbake til disponit_domene_eier, ville hver senere
    -- godkjenning/avvisning truffet permission denied i det nestede kallet og
    -- rullet tilbake selve domenevedtaket.
    ('FUNCTION', 'lukk_overtakelsessak(text,bigint,text,text)',       'disponit_domene_eier'),
    ('FUNCTION', 'degrader_forbigatte_utfordrere(text,text)',         'disponit_domene_eier'),
    -- Triggerfunksjonen på `hostname_binding` (019 §3.25) er SECURITY DEFINER
    -- og MÅ eies av samme rolle som funksjonen den kaller. Havnet den hos
    -- disponit_migrator ved andre kjøring av `oppsett-postgresql.sh`, ville
    -- degraderingen kjørt med migratorens rettigheter i stedet for
    -- domenelagets — en stille privilegieutvidelse på en vei ingen kaller
    -- eksplisitt.
    ('FUNCTION', 'trg_degrader_forbigatte_utfordrere()',              'disponit_domene_eier'),
    ('FUNCTION', 'antall_avgitte_attestasjoner(bigint,bigint)',       'disponit_domene_eier'),
    ('FUNCTION', 'rydd_staged_artefakter(integer)',                   'disponit_domene_eier'),
    ('FUNCTION', 'antall_karantenesatte()',                           'disponit_domene_eier'),
    -- Revalideringsscheduleren. Eid av domene_eier fordi den MÅ lese
    -- `domenekontroll` på tvers av tenanter (BYPASSRLS) for å regne budsjettet
    -- på riktig nevner — arbeiderrollen har bevisst ingen bordtilgang.
    ('FUNCTION', 'revalideringskandidater(integer,integer,integer,integer,integer)', 'disponit_domene_eier'),
    ('FUNCTION', 'revalideringspopulasjon()',                         'disponit_domene_eier'),
    -- Bevisporten foran 016s revalidering. Må ha SAMME eier som
    -- `revalider_domenekontroll(text,text,text)`: den delegerer til den
    -- nestet, og eierskapet er det eneste som gir den EXECUTE der.
    ('FUNCTION', 'revalider_domenekontroll(text,text,text,text[])',   'disponit_domene_eier'),
    -- 039: selvbetjent domeneverifisering — plukk + DB-holdt bevis
    ('FUNCTION', 'ventende_domenechallenges(integer)',                 'disponit_domene_eier'),
    ('FUNCTION', 'bekreft_domenechallenge(text,text,text,text[])',     'disponit_domene_eier'),
    ('FUNCTION', 'bekreft_overtakelseskonflikt(text,text,text,bigint)',     'disponit_domene_eier'),
    -- 041: overtakelsessaken. sikre_overtakelsessak er claimer-eid (én
    -- skrivevei til unntak/revisjonslogg, som resten av M-37-flaten.
    -- varsle_overtakelse er domene_eier-eid og kalles kun fra
    -- verifiser_domenekontroll (EXCEPTION-svelgende, port 41).
    ('FUNCTION', 'sikre_overtakelsessak(text,bigint,text,text,bigint,bigint,text,text)', 'disponit_m37_claimer'),
    -- 041 (målt): FORCE RLS filtrerer også eierens definer-lesing — de to
    -- vaktene bærer m37_dispatcher-policyen via claimer-eierskap.
    ('FUNCTION', 'unntak_lineage_matcher_loggpost()',              'disponit_m37_claimer'),
    ('FUNCTION', 'domenekontroll_krev_sak()',                      'disponit_m37_claimer'),
    ('FUNCTION', 'varsle_overtakelse(text,text,text,bigint)',          'disponit_domene_eier'),
    -- 041 (Codex P1): pre-041-konfliktene faar sin sak. BYPASSRLS-eid
    -- fordi skannet over domenekontroll er kryss-tenant. Kalles av
    -- migrasjonen og staar igjen som operatoerens reparasjonsvei.
    -- INGEN semikolon i denne kommentaren heller (se raden over)
    ('FUNCTION', 'migrer_pre041_overtakelseskonflikter(text)',         'disponit_domene_eier'),
    -- ... og arkivmerkingen av de gamle python-sakene er claimerens:
    -- den leser og skriver unntak_historikk, der domenelaget kun har
    -- INSERT. Maalt i CI, ikke resonnert frem.
    ('FUNCTION', 'arkivmerk_pre041_overtakelsessaker(text,text)',      'disponit_m37_claimer'),
    -- 041 §9.2 (Codex P2): vaktbikkjas ene spoersmaal. Claimer-eid av samme
    -- grunn som de to vaktene over — 9.1s RESTRICTIVE policy lukker den
    -- reserverte tenanten for alle andre enn claimer og eier, og en
    -- SECURITY DEFINER-funksjon ser radene som SIN EIER. Arbeideren faar
    -- EXECUTE, ikke leseflate
    ('FUNCTION', 'overtakelsessak_finnes(text,text,text,bigint)',      'disponit_m37_claimer'),
    -- 042 (Codex P1): adjudikasjonens to lesninger. Samme form og samme
    -- grunn som vakten over — de avloeser adjudikatorrollens uavgrensede
    -- SELECT paa unntak, og de MAA vaere claimer-eide for aa se
    -- plattformraden gjennom 9.1s allowlist. Eierskapet er ikke en detalj
    -- her: flyttes det, mister de synligheten og hver adjudikasjon svarer
    -- tomt — fail-closed, men stille. Omfanget leses av disponit.tenant,
    -- aldri av et argument
    ('FUNCTION', 'overtakelsessak_for_utfordrer(bigint)',              'disponit_m37_claimer'),
    ('FUNCTION', 'overtakelsessaker_for_utfordrer(timestamp with time zone,bigint,integer)', 'disponit_m37_claimer'),
    -- 039 (Codex P1): konflikter som venter paa sin M-37-sak. Kryss-tenant
    -- LESING, ingen p_tenant a velge — M-37-arbeideren drenerer dem.
    ('FUNCTION', 'ventende_overtakelseskonflikter(integer)',            'disponit_domene_eier'),
    -- 039 (Codex P1): den ENESTE utstedelsesformen runtime far. Binder
    -- p_tenant til kallerens tenantkontekst (krev_tenantkontekst, 038) —
    -- 016s raa utsted_challenge er REVOKEd fra runtime. INGEN semikolon i
    -- denne kommentaren: bade reparasjonen og pariteten deler filen paa
    -- setningsskilletegnet, og ett semikolon her kutter VALUES-listen.
    ('FUNCTION', 'utsted_challenge_selvbetjent(text,text,boolean,text,text)', 'disponit_domene_eier');

DO $$
DECLARE
    r RECORD;
BEGIN
    -- 1. Designobjekter til sin designede eier (retning: flatet -> design).
    --    Objekter som ikke finnes ennå (fersk base før migrasjonene)
    --    hoppes stille over — migrasjonene oppretter dem riktig selv.
    FOR r IN
        SELECT 'TABLE' AS art, c.oid::regclass::text AS ident, d.eier
          FROM _design d
          JOIN pg_class c ON d.art = 'TABLE'
           AND c.oid = to_regclass('public.' || d.ident)
         WHERE pg_get_userbyid(c.relowner) <> d.eier
        UNION ALL
        SELECT 'FUNCTION', p.oid::regprocedure::text, d.eier
          FROM _design d
          JOIN pg_proc p ON d.art = 'FUNCTION'
           AND p.oid = to_regprocedure('public.' || d.ident)
         WHERE pg_get_userbyid(p.proowner) <> d.eier
    LOOP
        EXECUTE format('ALTER %s %s OWNER TO %I', r.art, r.ident, r.eier);
        RAISE NOTICE 'eierskap: % % -> %', r.art, r.ident, r.eier;
    END LOOP;

    -- 2. Alt annet ikke-extension i public til migrator (retning:
    --    feilplassert/postgres/runtime -> migrator). Dekker også ordinære
    --    objekter som har havnet hos en privilegert rolle.
    FOR r IN
        SELECT 'TABLE' AS art, c.oid::regclass::text AS ident
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
           AND pg_get_userbyid(c.relowner) <> 'disponit_migrator'
           AND NOT EXISTS (SELECT 1 FROM _design d WHERE d.art = 'TABLE'
                            AND to_regclass('public.' || d.ident) = c.oid)
           AND NOT EXISTS (SELECT 1 FROM pg_depend dep
                            WHERE dep.classid = 'pg_class'::regclass
                              AND dep.objid = c.oid
                              AND dep.refclassid = 'pg_extension'::regclass
                              AND dep.deptype = 'e')
        UNION ALL
        SELECT 'FUNCTION', p.oid::regprocedure::text
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.prokind = 'f'
           AND pg_get_userbyid(p.proowner) <> 'disponit_migrator'
           -- Event-trigger-funksjoner er UTENFOR modellen med vilje:
           -- `disponit_vern_policy_retention` eies av superbrukeren
           -- nettopp for at migrator ikke skal kunne røre vakten
           -- (GO-vilkår V3). En reparasjon som tok den, ville demontert
           -- vernet den selv er en del av.
           AND p.prorettype <> 'event_trigger'::regtype
           AND NOT EXISTS (SELECT 1 FROM _design d WHERE d.art = 'FUNCTION'
                            AND to_regprocedure('public.' || d.ident) = p.oid)
           AND NOT EXISTS (SELECT 1 FROM pg_depend dep
                            WHERE dep.classid = 'pg_proc'::regclass
                              AND dep.objid = p.oid
                              AND dep.refclassid = 'pg_extension'::regclass
                              AND dep.deptype = 'e')
    LOOP
        EXECUTE format('ALTER %s %s OWNER TO %I', r.art, r.ident,
                       'disponit_migrator');
        RAISE NOTICE 'eierskap: % % -> disponit_migrator', r.art, r.ident;
    END LOOP;

    -- 3. SLUTTKONTROLL — fail-hard. Etter reparasjonen skal HVERT
    --    ikke-extension-objekt i public eies av nøyaktig sin designede
    --    eier eller migrator. Står noe igjen (f.eks. en eier reparatøren
    --    ikke har medlemskap i), skal kjøringen FEILE — ikke advare.
    FOR r IN
        SELECT c.oid::regclass::text AS ident,
               pg_get_userbyid(c.relowner) AS eier
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
           AND pg_get_userbyid(c.relowner) <> COALESCE(
                   (SELECT d.eier FROM _design d WHERE d.art = 'TABLE'
                     AND to_regclass('public.' || d.ident) = c.oid),
                   'disponit_migrator')
           AND NOT EXISTS (SELECT 1 FROM pg_depend dep
                            WHERE dep.classid = 'pg_class'::regclass
                              AND dep.objid = c.oid
                              AND dep.refclassid = 'pg_extension'::regclass
                              AND dep.deptype = 'e')
        UNION ALL
        SELECT p.oid::regprocedure::text, pg_get_userbyid(p.proowner)
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.prokind = 'f'
           AND p.prorettype <> 'event_trigger'::regtype   -- V3, som over
           AND pg_get_userbyid(p.proowner) <> COALESCE(
                   (SELECT d.eier FROM _design d WHERE d.art = 'FUNCTION'
                     AND to_regprocedure('public.' || d.ident) = p.oid),
                   'disponit_migrator')
           AND NOT EXISTS (SELECT 1 FROM pg_depend dep
                            WHERE dep.classid = 'pg_proc'::regclass
                              AND dep.objid = p.oid
                              AND dep.refclassid = 'pg_extension'::regclass
                              AND dep.deptype = 'e')
    LOOP
        RAISE EXCEPTION
            'eierskapsreparasjon: % eies fortsatt av % — utenfor designmodellen',
            r.ident, r.eier;
    END LOOP;
END $$;

COMMIT;
