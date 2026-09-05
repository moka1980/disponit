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
    -- 094 (M-5): malregisterets fem doerer eies av mal_eier — samme
    -- vindu og samme grunn som 057/082/089-doerene over, men EGEN eier
    -- fordi malregisteret ikke deler lager med noen av dem. Vaktene
    -- (m5_familie_vakt, m5_versjon_vakt, m5_innhold_vakt) staar bevisst
    -- IKKE her: de er triggerfunksjoner laget FOER rolleblokken og eies
    -- av migrator som alle andre vakter. m5_fyll_mal er STABLE og kan
    -- derfor ikke skrive i det hele tatt — eierskapet gir den lesing
    -- gjennom FORCE RLS, ingenting mer.
    ('FUNCTION', 'm5_opprett_malfamilie(text,text,text,text,uuid)', 'disponit_mal_eier'),
    ('FUNCTION', 'm5_opprett_malversjon(text,uuid,jsonb,jsonb,text,uuid)', 'disponit_mal_eier'),
    ('FUNCTION', 'm5_publiser_malversjon(text,uuid,text)', 'disponit_mal_eier'),
    ('FUNCTION', 'm5_trekk_tilbake_malversjon(text,uuid,text)', 'disponit_mal_eier'),
    ('FUNCTION', 'm5_fyll_mal(text,uuid,jsonb)', 'disponit_mal_eier'),
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
    -- 092 (M-3): datakvalitetens fire tabeller og seks funksjoner. Her er
    -- eierskapet ikke en formalitet, men selve sikkerhetsgrensen:
    -- profileringsdoeren er SECURITY DEFINER, og det er EIERENS
    -- kolonnegrants -- tenant + de profilerte kolonnene, aldri en
    -- payloadkolonne -- som avgjoer hva jobben kan lese. Flyttet noen
    -- disse til migrator, ville doeren kjoert med eierens rettigheter paa
    -- alt, og hele modellen vaert borte uten at en eneste linje kode var
    -- endret
    ('TABLE',    'kvalitetsregel',                 'disponit_kvalitet_eier'),
    ('TABLE',    'kvalitetskjoring',               'disponit_kvalitet_eier'),
    ('TABLE',    'kvalitetsprofil',                'disponit_kvalitet_eier'),
    ('TABLE',    'kvalitetsfunn',                  'disponit_kvalitet_eier'),
    ('FUNCTION', 'm3_profiler(integer)',                          'disponit_kvalitet_eier'),
    ('FUNCTION', 'm3_reis_funn(text,text,text,uuid,jsonb)',       'disponit_kvalitet_eier'),
    ('FUNCTION', 'm3_regelregister(text)',                        'disponit_kvalitet_eier'),
    ('FUNCTION', 'm3_kvalitetsprofil(text,integer)',              'disponit_kvalitet_eier'),
    ('FUNCTION', 'm3_kvalitetsfunn(text,integer)',                'disponit_kvalitet_eier'),
    ('FUNCTION', 'm3_kvalitetsfunn_tverrgaaende(text,integer)',   'disponit_kvalitet_eier'),
    ('FUNCTION', 'm3_regel_vakt()',                               'disponit_kvalitet_eier'),
    ('FUNCTION', 'm3_kjoring_vakt()',                             'disponit_kvalitet_eier'),
    ('FUNCTION', 'm3_profil_vakt()',                              'disponit_kvalitet_eier'),
    ('FUNCTION', 'm3_funn_vakt()',                                'disponit_kvalitet_eier'),
    -- 095 (M-9): begrepsregisterets fem doerer. De eies av modulens EGEN
    -- rolle, ikke av claimeren -- eierskapet ER tabelltilgangen her, og
    -- en modul som eier sine egne doerer kan ikke naa en annen moduls
    -- rader ved et uhell. De tre tenantbundne doerene gaar gjennom
    -- krev_tenantkontekst-porten, som er claimer-eid -- 095 gir derfor
    -- kunnskap_eier EXECUTE paa porten, den maa ikke eie den.
    -- Sveipen deler eier med de andre fordi den deler tabell, og fordi
    -- kryss-tenant-lesingen er en policy som navngir NOEYAKTIG denne
    -- rollen (057/088-radenes egen begrunnelse, med et snevrere vindu)
    ('FUNCTION', 'm9_registrer_begrep(text,text,text,text,text,date,text,uuid)', 'disponit_kunnskap_eier'),
    ('FUNCTION', 'm9_ny_begrepsversjon(text,text,text,text,text,date,text,uuid)', 'disponit_kunnskap_eier'),
    ('FUNCTION', 'm9_sok(text,text,integer)',                         'disponit_kunnskap_eier'),
    ('FUNCTION', 'm9_apne_funn(text,integer)',                        'disponit_kunnskap_eier'),
    ('FUNCTION', 'm9_sveip_utlopte(integer)',                         'disponit_kunnskap_eier'),
    -- 096 (M-21): pliktregisterets doerer og fristsveipen. NOLOGIN-eieren
    -- `disponit_plikt_eier` eier funksjonene, ikke tabellene -- de tre
    -- tabellene er migrators, som 089/090/091-familien. Eierskapet ER
    -- skrivetilgangen her (057-radenes begrunnelse): runtime har ingen
    -- tabellrettighet paa registeret i det hele tatt og naar det KUN
    -- gjennom disse doerene, og sveipen er den innelukkede
    -- kryss-tenant-autoriteten senderrollen alene faar kalle.
    -- Trigger-vaktene `m21_plikt_vakt`/`m21_anker_vakt` staar bevisst
    -- IKKE her: de opprettes utenfor SET ROLE-vinduet og er migrators,
    -- som resten av husets radvakter.
    ('FUNCTION', 'm21_evidens(text,uuid,text,text,jsonb)',             'disponit_plikt_eier'),
    ('FUNCTION', 'm21_standardpunkter()',                              'disponit_plikt_eier'),
    ('FUNCTION', 'm21_neste_frist(timestamp with time zone,text)',     'disponit_plikt_eier'),
    ('FUNCTION', 'm21_registrer_plikt(text,uuid,text,text,text,timestamp with time zone,text,integer[],text)', 'disponit_plikt_eier'),
    ('FUNCTION', 'm21_lukk_plikt(text,uuid,text,text)',                'disponit_plikt_eier'),
    ('FUNCTION', 'm21_marker_bortfalt(text,uuid,text,text)',           'disponit_plikt_eier'),
    ('FUNCTION', 'm21_plikter(text,integer)',                          'disponit_plikt_eier'),
    ('FUNCTION', 'm21_koe_for_tenant(text,integer)',                   'disponit_plikt_eier'),
    ('FUNCTION', 'm21_koe_fristvarsler(integer)',                      'disponit_plikt_eier'),
    -- 097 (M-12): tilgangsregisterets doerer og gjennomgangssveipen.
    -- Samme form som 096-blokken over: NOLOGIN-eieren
    -- `disponit_tilgang_eier` eier funksjonene, ikke tabellene -- de tre
    -- tabellene er migrators, som resten av 089-096-familien. Eierskapet
    -- ER skrivetilgangen: runtime har ingen tabellrettighet paa
    -- registeret i det hele tatt og naar det KUN gjennom disse doerene.
    -- `m12_sveip_gjennomganger` er den innelukkede
    -- kryss-tenant-autoriteten sveiperollen alene faar kalle, og
    -- `m12_sveip_for_tenant` er armen den bruker innenfra -- ingen av
    -- dem er grantet til runtime.
    -- Trigger-vaktene `m12_objekt_vakt`, `m12_tilgang_vakt` og
    -- `m12_funn_vakt` staar bevisst IKKE her: de opprettes utenfor
    -- SET ROLE-vinduet og er migrators, som resten av husets radvakter.
    ('FUNCTION', 'm12_evidens(text,uuid,text,text,jsonb)',             'disponit_tilgang_eier'),
    ('FUNCTION', 'm12_registrer_objekt(text,uuid,text,text,text,text)', 'disponit_tilgang_eier'),
    ('FUNCTION', 'm12_registrer_tilgang(text,uuid,uuid,text,text,text,text,text,integer,text)', 'disponit_tilgang_eier'),
    ('FUNCTION', 'm12_registrer_gjennomgang(text,uuid,text)',          'disponit_tilgang_eier'),
    ('FUNCTION', 'm12_tilgangsbilde(text,integer)',                    'disponit_tilgang_eier'),
    ('FUNCTION', 'm12_objekter(text,integer)',                         'disponit_tilgang_eier'),
    ('FUNCTION', 'm12_apne_funn(text,integer)',                        'disponit_tilgang_eier'),
    ('FUNCTION', 'm12_sveip_for_tenant(text,integer)',                 'disponit_tilgang_eier'),
    ('FUNCTION', 'm12_sveip_gjennomganger(integer)',                   'disponit_tilgang_eier'),
    -- 098 (M-22): lisensregisterets doerer og utloepssveipen. Samme form
    -- og samme begrunnelse som 096-raden over, med M-22s egen NOLOGIN-
    -- eier: `disponit_lisens_eier` eier funksjonene, ikke tabellene --
    -- de tre tabellene er migrators. Runtime har ingen tabellrettighet
    -- paa registeret i det hele tatt og naar det KUN gjennom disse
    -- doerene, og sveipen er den innelukkede kryss-tenant-autoriteten
    -- senderrollen alene faar kalle. Vaktene `m22_lisens_vakt` og
    -- `m22_anker_vakt` staar bevisst IKKE her: de opprettes utenfor
    -- SET ROLE-vinduet og er migrators, som resten av husets radvakter.
    ('FUNCTION', 'm22_evidens(text,uuid,text,text,jsonb)',             'disponit_lisens_eier'),
    ('FUNCTION', 'm22_standardpunkter()',                              'disponit_lisens_eier'),
    ('FUNCTION', 'm22_registrer_lisens(text,uuid,text,text,text,integer,numeric,text,date,text,integer,text,integer[],text)', 'disponit_lisens_eier'),
    ('FUNCTION', 'm22_registrer_fornyelse(text,uuid,date,text)',       'disponit_lisens_eier'),
    ('FUNCTION', 'm22_marker_avsluttet(text,uuid,text,text)',          'disponit_lisens_eier'),
    ('FUNCTION', 'm22_lisenser(text,integer)',                         'disponit_lisens_eier'),
    ('FUNCTION', 'm22_koe_for_tenant(text,integer)',                   'disponit_lisens_eier'),
    ('FUNCTION', 'm22_koe_utlopsvarsler(integer)',                     'disponit_lisens_eier'),
    -- 099 (M-30): forespoerselsregisterets doerer, fristsveipen og
    -- M-4-vakten. NOLOGIN-eieren `disponit_personvern_eier` eier
    -- funksjonene, ikke tabellene -- de tre tabellene er migrators, som
    -- 089/090/091/096-familien. Eierskapet ER skrivetilgangen her
    -- (057-radenes begrunnelse): runtime har ingen tabellrettighet paa
    -- registeret i det hele tatt og naar det KUN gjennom disse doerene,
    -- og sveipen er den innelukkede kryss-tenant-autoriteten
    -- sveiperollen alene faar kalle.
    -- `m30_lager_vakt` STAAR her, til forskjell fra radvaktene
    -- `m30_sak_vakt`/`m30_funn_vakt` som er migrators: den er SECURITY
    -- DEFINER og leser M-4s `retensjonslager` paa vegne av hvem som
    -- helst som skriver koblingen, og eierskapet ER den lesetilgangen
    -- (093s egen begrunnelse for sine to registervakter, ordrett)
    ('FUNCTION', 'm30_evidens(text,uuid,text,text,jsonb)',              'disponit_personvern_eier'),
    ('FUNCTION', 'm30_ordinaer_frist(date)',                           'disponit_personvern_eier'),
    ('FUNCTION', 'm30_lager_vakt()',                                   'disponit_personvern_eier'),
    ('FUNCTION', 'm30_registrer_sak(text,uuid,text,text,date,text,text[],text)', 'disponit_personvern_eier'),
    ('FUNCTION', 'm30_besvar_sak(text,uuid,text,text)',                'disponit_personvern_eier'),
    ('FUNCTION', 'm30_avvis_sak(text,uuid,text,text)',                 'disponit_personvern_eier'),
    ('FUNCTION', 'm30_forleng_frist(text,uuid,date,text,text)',        'disponit_personvern_eier'),
    ('FUNCTION', 'm30_saker(text,integer)',                            'disponit_personvern_eier'),
    ('FUNCTION', 'm30_apne_funn(text,integer)',                        'disponit_personvern_eier'),
    ('FUNCTION', 'm30_sveipkandidater(text,date,integer)',             'disponit_personvern_eier'),
    ('FUNCTION', 'm30_sveip_frister(integer)',                         'disponit_personvern_eier'),
    -- 100 (M-34): kontrollregisterets doerer og etterproevingssveipen.
    -- NOLOGIN-eieren `disponit_compliance_eier` eier funksjonene, ikke
    -- tabellene -- de fire tabellene er migrators, som 095/096-familien.
    -- Eierskapet ER skrivetilgangen her (057-radenes begrunnelse):
    -- runtime har ingen tabellrettighet paa registeret i det hele tatt
    -- og naar det KUN gjennom disse doerene, og sveipen er den
    -- innelukkede kryss-tenant-autoriteten sveiperollen alene faar kalle
    -- Trigger-vaktene `m34_kontroll_vakt`, `m34_etterproving_vakt` og
    -- `m34_funn_vakt` staar bevisst IKKE her: de opprettes utenfor
    -- SET ROLE-vinduet og er migrators, som resten av husets radvakter
    ('FUNCTION', 'm34_evidens(text,uuid,text,text,jsonb)',             'disponit_compliance_eier'),
    ('FUNCTION', 'm34_rammeverk_id(text,text,text,text)',              'disponit_compliance_eier'),
    ('FUNCTION', 'm34_registrer_kontroll(text,uuid,text,text,text,text,text,integer,text)', 'disponit_compliance_eier'),
    ('FUNCTION', 'm34_registrer_etterproving(text,uuid,uuid,date,text,text,text,text,text)', 'disponit_compliance_eier'),
    ('FUNCTION', 'm34_marker_ikke_relevant(text,uuid,text,text)',      'disponit_compliance_eier'),
    ('FUNCTION', 'm34_kontrollbilde(text,integer)',                    'disponit_compliance_eier'),
    ('FUNCTION', 'm34_funnkandidater(text,date)',                      'disponit_compliance_eier'),
    ('FUNCTION', 'm34_sveip_etterprovinger(integer)',                  'disponit_compliance_eier'),
    -- 101 (M-13): avstemmingsregisterets doerer og avstemmingssveipen.
    -- NOLOGIN-eieren `disponit_avstemming_eier` eier funksjonene, ikke
    -- tabellene -- de fem tabellene er migrators, som 095/096/100-familien.
    -- Eierskapet ER skrivetilgangen her (057-radenes begrunnelse):
    -- runtime har ingen tabellrettighet paa registeret i det hele tatt
    -- og naar det KUN gjennom disse doerene, og sveipen er den
    -- innelukkede kryss-tenant-autoriteten sveiperollen alene faar kalle
    -- Trigger-vaktene `m13_bankpost_vakt`, `m13_bilag_vakt`,
    -- `m13_avstemming_vakt` og `m13_funn_vakt` staar bevisst IKKE her: de
    -- opprettes utenfor SET ROLE-vinduet og er migrators, som resten av
    -- husets radvakter
    ('FUNCTION', 'm13_evidens(text,uuid,text,text,jsonb)',             'disponit_avstemming_eier'),
    ('FUNCTION', 'm13_registrer_konto(text,uuid,text,text,text,text)', 'disponit_avstemming_eier'),
    ('FUNCTION', 'm13_registrer_post(text,uuid,uuid,text,date,bigint,text,text,text)', 'disponit_avstemming_eier'),
    ('FUNCTION', 'm13_registrer_bilag(text,uuid,text,text,bigint,text,date,date,text)', 'disponit_avstemming_eier'),
    ('FUNCTION', 'm13_avstem(text,uuid,uuid,uuid,text,text,text)',      'disponit_avstemming_eier'),
    ('FUNCTION', 'm13_opphev_avstemming(text,uuid,text,text)',          'disponit_avstemming_eier'),
    ('FUNCTION', 'm13_avstemmingsstatus(text)',                         'disponit_avstemming_eier'),
    ('FUNCTION', 'm13_kontoer(text)',                                   'disponit_avstemming_eier'),
    ('FUNCTION', 'm13_uavstemte_poster(text,integer)',                  'disponit_avstemming_eier'),
    ('FUNCTION', 'm13_apne_bilag(text,integer)',                        'disponit_avstemming_eier'),
    ('FUNCTION', 'm13_funnkandidater(text,date,integer)',               'disponit_avstemming_eier'),
    ('FUNCTION', 'm13_sveip_avstemming(integer,integer)',               'disponit_avstemming_eier'),
    -- 102 (M-17): henvendelsesregisterets doerer og henvendelsessveipen.
    -- NOLOGIN-eieren `disponit_kundeservice_eier` eier funksjonene, ikke
    -- tabellene. Eierskapet ER skrivetilgangen (057-radenes begrunnelse):
    -- runtime har ingen tabellrettighet paa registeret i det hele tatt
    -- Vaktene `m17_henvendelse_vakt`, `m17_klassifisering_vakt`,
    -- `m17_utkast_vakt` og `m17_funn_vakt` staar bevisst IKKE her: de
    -- opprettes utenfor SET ROLE-vinduet og er migrators
    ('FUNCTION', 'm17_evidens(text,uuid,text,text,jsonb)',              'disponit_kundeservice_eier'),
    ('FUNCTION', 'm17_ta_imot(text,uuid,text,text,timestamp with time zone,text,bytea,bytea,bytea,bytea,text,text)', 'disponit_kundeservice_eier'),
    ('FUNCTION', 'm17_klassifiser(text,uuid,text,text,text,text,text,text)', 'disponit_kundeservice_eier'),
    ('FUNCTION', 'm17_til_unntakskoe(text,uuid,text,bytea,bytea,text,text)', 'disponit_kundeservice_eier'),
    ('FUNCTION', 'm17_lagre_utkast(text,uuid,uuid,bytea,bytea,text,text[],text,text,text)', 'disponit_kundeservice_eier'),
    ('FUNCTION', 'm17_avgjor_utkast(text,uuid,text,text)',              'disponit_kundeservice_eier'),
    ('FUNCTION', 'm17_lukk(text,uuid,text,text)',                       'disponit_kundeservice_eier'),
    ('FUNCTION', 'm17_kostatus(text)',                                  'disponit_kundeservice_eier'),
    ('FUNCTION', 'm17_koen(text,integer)',                              'disponit_kundeservice_eier'),
    ('FUNCTION', 'm17_hent_innhold(text,uuid)',                         'disponit_kundeservice_eier'),
    ('FUNCTION', 'm17_utkastene(text,uuid)',                            'disponit_kundeservice_eier'),
    ('FUNCTION', 'm17_funnkandidater(text,date,integer,integer)',       'disponit_kundeservice_eier'),
    ('FUNCTION', 'm17_sveip_henvendelser(integer,integer,integer)',     'disponit_kundeservice_eier'),
    -- 103 (M-18): onboardingregisterets doerer og onboardingsveipen.
    -- NOLOGIN-eieren `disponit_onboarding_eier` eier funksjonene, ikke
    -- tabellene. Vaktene `m18_malsteg_vakt`, `m18_lop_vakt`,
    -- `m18_steg_vakt` og `m18_funn_vakt` staar bevisst IKKE her: de
    -- opprettes utenfor SET ROLE-vinduet og er migrators
    ('FUNCTION', 'm18_evidens(text,uuid,text,text,jsonb)',              'disponit_onboarding_eier'),
    ('FUNCTION', 'm18_registrer_mal(text,uuid,text,text)',              'disponit_onboarding_eier'),
    ('FUNCTION', 'm18_sett_malsteg(text,uuid,jsonb,text)',              'disponit_onboarding_eier'),
    ('FUNCTION', 'm18_start_lop(text,uuid,uuid,text,text,date,text)',   'disponit_onboarding_eier'),
    ('FUNCTION', 'm18_sett_stegeier(text,uuid,integer,text,text)',      'disponit_onboarding_eier'),
    ('FUNCTION', 'm18_fullfor_steg(text,uuid,integer,text,text)',       'disponit_onboarding_eier'),
    ('FUNCTION', 'm18_avslutt_lop(text,uuid,text,text,text)',           'disponit_onboarding_eier'),
    ('FUNCTION', 'm18_onboardingstatus(text)',                          'disponit_onboarding_eier'),
    ('FUNCTION', 'm18_lopene(text,integer)',                            'disponit_onboarding_eier'),
    ('FUNCTION', 'm18_stegene(text,uuid)',                              'disponit_onboarding_eier'),
    ('FUNCTION', 'm18_malene(text)',                                    'disponit_onboarding_eier'),
    ('FUNCTION', 'm18_funnkandidater(text,date,integer)',               'disponit_onboarding_eier'),
    ('FUNCTION', 'm18_sveip_onboarding(integer,integer)',               'disponit_onboarding_eier'),
    -- 104 (M-23): fordringsregisterets doerer og fordringssveipen.
    -- Vaktene `m23_purretrinn_vakt`, `m23_fordring_vakt`,
    -- `m23_hendelse_vakt` og `m23_funn_vakt` staar bevisst IKKE her: de
    -- opprettes utenfor SET ROLE-vinduet og er migrators
    ('FUNCTION', 'm23_evidens(text,uuid,text,text,jsonb)',              'disponit_fordring_eier'),
    ('FUNCTION', 'm23_sett_purreplan(text,jsonb,text)',                 'disponit_fordring_eier'),
    ('FUNCTION', 'm23_registrer_fordring(text,uuid,text,text,bigint,date,date,text)', 'disponit_fordring_eier'),
    ('FUNCTION', 'm23_registrer_betaling(text,uuid,uuid,bigint,date,text)', 'disponit_fordring_eier'),
    ('FUNCTION', 'm23_neste_trinn(text,uuid,uuid,text,text)',           'disponit_fordring_eier'),
    ('FUNCTION', 'm23_ettergi(text,uuid,uuid,text,text)',               'disponit_fordring_eier'),
    ('FUNCTION', 'm23_fordringsstatus(text)',                           'disponit_fordring_eier'),
    ('FUNCTION', 'm23_aldersfordeling(text)',                           'disponit_fordring_eier'),
    ('FUNCTION', 'm23_fordringene(text,integer)',                       'disponit_fordring_eier'),
    ('FUNCTION', 'm23_purreplanen(text)',                               'disponit_fordring_eier'),
    ('FUNCTION', 'm23_hendelsene(text,uuid)',                           'disponit_fordring_eier'),
    ('FUNCTION', 'm23_funnkandidater(text,date)',                       'disponit_fordring_eier'),
    ('FUNCTION', 'm23_sveip_fordringer(integer)',                       'disponit_fordring_eier'),
    -- 105 (M-24): leverandorregisterets doerer og leverandorsveipen.
    -- Vaktene `m24_terskel_vakt`, `m24_part_vakt`, `m24_avtale_vakt`,
    -- `m24_leveranse_vakt` og `m24_funn_vakt` staar bevisst IKKE her: de
    -- opprettes utenfor SET ROLE-vinduet og er migrators.
    ('FUNCTION', 'm24_bryter_sla(text,integer,integer)',                'disponit_leverandor_eier'),
    ('FUNCTION', 'm24_evidens(text,uuid,text,text,jsonb)',              'disponit_leverandor_eier'),
    ('FUNCTION', 'm24_sett_terskler(text,integer,integer,integer,integer,text)', 'disponit_leverandor_eier'),
    ('FUNCTION', 'm24_registrer_leverandor(text,uuid,text,text,text)',  'disponit_leverandor_eier'),
    ('FUNCTION', 'm24_registrer_avtale(text,uuid,uuid,text,text,integer,bigint,date,date,text)', 'disponit_leverandor_eier'),
    ('FUNCTION', 'm24_registrer_leveranse(text,uuid,uuid,date,integer,bigint,text,text)', 'disponit_leverandor_eier'),
    ('FUNCTION', 'm24_avslutt_avtale(text,uuid,text,text)',             'disponit_leverandor_eier'),
    ('FUNCTION', 'm24_leverandorstatus(text)',                          'disponit_leverandor_eier'),
    ('FUNCTION', 'm24_slaoversikt(text)',                               'disponit_leverandor_eier'),
    ('FUNCTION', 'm24_avtalene(text,integer)',                          'disponit_leverandor_eier'),
    ('FUNCTION', 'm24_leveransene(text,uuid)',                          'disponit_leverandor_eier'),
    ('FUNCTION', 'm24_tersklene(text)',                                 'disponit_leverandor_eier'),
    ('FUNCTION', 'm24_leverandorene(text)',                             'disponit_leverandor_eier'),
    ('FUNCTION', 'm24_funnkandidater(text,date)',                       'disponit_leverandor_eier'),
    ('FUNCTION', 'm24_sveip_leverandorer(integer)',                     'disponit_leverandor_eier'),
    -- 106 (M-14): fakturaregisterets doerer og fakturasveipen. Vaktene
    -- staar bevisst IKKE her: de opprettes utenfor SET ROLE-vinduet og
    -- er migrators.
    ('FUNCTION', 'm14_forventet_mva(bigint,integer)',                   'disponit_faktura_eier'),
    ('FUNCTION', 'm14_sats_paa_dato(text,text,date)',                   'disponit_faktura_eier'),
    ('FUNCTION', 'm14_utled_kontroll(uuid,text)',                       'disponit_faktura_eier'),
    ('FUNCTION', 'm14_leverandor_kjent(text,text)',                     'disponit_faktura_eier'),
    ('FUNCTION', 'm14_evidens(text,uuid,text,text,jsonb)',              'disponit_faktura_eier'),
    ('FUNCTION', 'm14_sett_terskler(text,bigint,bigint,integer,integer,text)', 'disponit_faktura_eier'),
    ('FUNCTION', 'm14_sett_mvasats(text,text,integer,date,date,text)',  'disponit_faktura_eier'),
    ('FUNCTION', 'm14_registrer_faktura(text,uuid,text,text,bigint,bigint,bigint,text,text,date,date,date,text)', 'disponit_faktura_eier'),
    ('FUNCTION', 'm14_registrer_kontroll(text,uuid,uuid,text,text,text)', 'disponit_faktura_eier'),
    ('FUNCTION', 'm14_avgjor_faktura(text,uuid,text,text,text)',        'disponit_faktura_eier'),
    ('FUNCTION', 'm14_fakturastatus(text)',                             'disponit_faktura_eier'),
    ('FUNCTION', 'm14_treffrate(text)',                                 'disponit_faktura_eier'),
    ('FUNCTION', 'm14_fakturaene(text,integer)',                        'disponit_faktura_eier'),
    ('FUNCTION', 'm14_kontrollene(text,uuid)',                          'disponit_faktura_eier'),
    ('FUNCTION', 'm14_tersklene(text)',                                 'disponit_faktura_eier'),
    ('FUNCTION', 'm14_satsene(text)',                                   'disponit_faktura_eier'),
    ('FUNCTION', 'm14_funnkandidater(text,date)',                       'disponit_faktura_eier'),
    ('FUNCTION', 'm14_sveip_fakturaer(integer)',                        'disponit_faktura_eier'),
    -- 107 (M-25): prosjektregisterets doerer og prosjektsveipen.
    ('FUNCTION', 'm25_evidens(text,uuid,text,text,jsonb)',              'disponit_prosjekt_eier'),
    ('FUNCTION', 'm25_sett_terskler(text,integer,integer,integer,text)', 'disponit_prosjekt_eier'),
    ('FUNCTION', 'm25_registrer_prosjekt(text,uuid,text,text,text,bigint,date,date,text)', 'disponit_prosjekt_eier'),
    ('FUNCTION', 'm25_sett_betalingsplan(text,uuid,jsonb,text)',        'disponit_prosjekt_eier'),
    ('FUNCTION', 'm25_naa_milepael(text,uuid,integer,text,text)',       'disponit_prosjekt_eier'),
    ('FUNCTION', 'm25_registrer_arbeid(text,uuid,uuid,date,integer,bigint,text,text)', 'disponit_prosjekt_eier'),
    ('FUNCTION', 'm25_avslutt_prosjekt(text,uuid,text,text)',           'disponit_prosjekt_eier'),
    ('FUNCTION', 'm25_prosjektstatus(text)',                            'disponit_prosjekt_eier'),
    ('FUNCTION', 'm25_prosjektene(text,integer)',                       'disponit_prosjekt_eier'),
    ('FUNCTION', 'm25_milepaelene(text,uuid)',                          'disponit_prosjekt_eier'),
    ('FUNCTION', 'm25_arbeidet(text,uuid)',                             'disponit_prosjekt_eier'),
    ('FUNCTION', 'm25_tersklene(text)',                                 'disponit_prosjekt_eier'),
    ('FUNCTION', 'm25_funnkandidater(text,date)',                       'disponit_prosjekt_eier'),
    ('FUNCTION', 'm25_sveip_prosjekter(integer)',                       'disponit_prosjekt_eier'),
    -- 108 (M-26): prisbokas doerer og prisboksveipen.
    ('FUNCTION', 'm26_evidens(text,uuid,text,text,jsonb)',              'disponit_prisbok_eier'),
    ('FUNCTION', 'm26_sett_terskler(text,integer,integer,integer,text)', 'disponit_prisbok_eier'),
    ('FUNCTION', 'm26_registrer_produkt(text,uuid,text,text,text,text)', 'disponit_prisbok_eier'),
    ('FUNCTION', 'm26_sett_pris(text,uuid,bigint,text,date,text,text)',  'disponit_prisbok_eier'),
    ('FUNCTION', 'm26_sett_klausul(text,text,text,text,boolean,date,text)', 'disponit_prisbok_eier'),
    ('FUNCTION', 'm26_sett_produktaktiv(text,uuid,boolean,text)',        'disponit_prisbok_eier'),
    ('FUNCTION', 'm26_pris_paa_dato(text,uuid,date)',                    'disponit_prisbok_eier'),
    ('FUNCTION', 'm26_innenfor_rabatt(text,uuid,date,bigint)',           'disponit_prisbok_eier'),
    ('FUNCTION', 'm26_prisbokstatus(text)',                              'disponit_prisbok_eier'),
    ('FUNCTION', 'm26_produktene(text,integer)',                         'disponit_prisbok_eier'),
    ('FUNCTION', 'm26_prishistorikken(text,uuid)',                       'disponit_prisbok_eier'),
    ('FUNCTION', 'm26_klausulene(text)',                                 'disponit_prisbok_eier'),
    ('FUNCTION', 'm26_tersklene(text)',                                  'disponit_prisbok_eier'),
    ('FUNCTION', 'm26_funnkandidater(text,date)',                        'disponit_prisbok_eier'),
    ('FUNCTION', 'm26_sveip_prisbok(integer)',                           'disponit_prisbok_eier'),
    -- 109 (M-27): lagerregisterets doerer og lagersveipen.
    ('FUNCTION', 'm27_evidens(text,uuid,text,text,jsonb)',              'disponit_beholdning_eier'),
    ('FUNCTION', 'm27_sett_terskler(text,integer,integer,integer,text)', 'disponit_beholdning_eier'),
    ('FUNCTION', 'm27_registrer_vare(text,uuid,text,text,text,text)',    'disponit_beholdning_eier'),
    ('FUNCTION', 'm27_sett_bestillingspunkt(text,uuid,bigint,date,text,text)', 'disponit_beholdning_eier'),
    ('FUNCTION', 'm27_registrer_bevegelse(text,uuid,uuid,text,bigint,bigint,date,text,text)', 'disponit_beholdning_eier'),
    ('FUNCTION', 'm27_registrer_telling(text,uuid,uuid,bigint,date,text,text)', 'disponit_beholdning_eier'),
    ('FUNCTION', 'm27_sett_vareaktiv(text,uuid,boolean,text)',           'disponit_beholdning_eier'),
    ('FUNCTION', 'm27_beholdning(text,uuid)',                            'disponit_beholdning_eier'),
    ('FUNCTION', 'm27_beholdning_paa_dato(text,uuid,date)',              'disponit_beholdning_eier'),
    ('FUNCTION', 'm27_punkt_paa_dato(text,uuid,date)',                   'disponit_beholdning_eier'),
    ('FUNCTION', 'm27_under_bestillingspunkt(text,uuid,date)',           'disponit_beholdning_eier'),
    ('FUNCTION', 'm27_lagerstatus(text)',                                'disponit_beholdning_eier'),
    ('FUNCTION', 'm27_varene(text,integer)',                             'disponit_beholdning_eier'),
    ('FUNCTION', 'm27_bevegelsene(text,uuid,integer)',                   'disponit_beholdning_eier'),
    ('FUNCTION', 'm27_tersklene(text)',                                  'disponit_beholdning_eier'),
    ('FUNCTION', 'm27_funnkandidater(text,date)',                        'disponit_beholdning_eier'),
    ('FUNCTION', 'm27_sveip_lager(integer)',                             'disponit_beholdning_eier'),
    -- 110 (M-42): kontoregisterets doerer og kontovaktsveipen.
    ('FUNCTION', 'm42_evidens(text,uuid,text,text,jsonb)',              'disponit_kontovakt_eier'),
    ('FUNCTION', 'm42_normaliser(text)',                                'disponit_kontovakt_eier'),
    ('FUNCTION', 'm42_sett_terskler(text,integer,integer,text)',        'disponit_kontovakt_eier'),
    ('FUNCTION', 'm42_registrer_mottaker(text,uuid,text,text,text)',    'disponit_kontovakt_eier'),
    ('FUNCTION', 'm42_oppgi_konto(text,uuid,uuid,text,text,text,date,text,text)', 'disponit_kontovakt_eier'),
    ('FUNCTION', 'm42_verifiser_konto(text,uuid,uuid,text,text,text,date,text)', 'disponit_kontovakt_eier'),
    ('FUNCTION', 'm42_sett_mottakeraktiv(text,uuid,boolean,text)',       'disponit_kontovakt_eier'),
    ('FUNCTION', 'm42_gjeldende_konto(text,uuid)',                       'disponit_kontovakt_eier'),
    ('FUNCTION', 'm42_kontohistorikken(text,uuid,integer)',              'disponit_kontovakt_eier'),
    ('FUNCTION', 'm42_kontostatus(text)',                                'disponit_kontovakt_eier'),
    ('FUNCTION', 'm42_mottakerne(text,integer)',                         'disponit_kontovakt_eier'),
    ('FUNCTION', 'm42_tersklene(text)',                                  'disponit_kontovakt_eier'),
    ('FUNCTION', 'm42_funnkandidater(text,date)',                        'disponit_kontovakt_eier'),
    ('FUNCTION', 'm42_sveip_konto(integer)',                             'disponit_kontovakt_eier'),
    -- 111 (M-41): betalingsregisterets doerer og betalingssveipen.
    ('FUNCTION', 'm41_evidens(text,uuid,text,text,jsonb)',              'disponit_betaling_eier'),
    ('FUNCTION', 'm41_normaliser(text)',                                'disponit_betaling_eier'),
    ('FUNCTION', 'm41_sett_terskler(text,integer,bigint,integer,text)', 'disponit_betaling_eier'),
    ('FUNCTION', 'm41_registrer_subjekt(text,uuid,text,text,text)',     'disponit_betaling_eier'),
    ('FUNCTION', 'm41_registrer_status(text,uuid,uuid,text,bigint,bigint,text,text,text,text,date,text,text)', 'disponit_betaling_eier'),
    ('FUNCTION', 'm41_sett_abonnementsstatus(text,uuid,text,date,text,text)', 'disponit_betaling_eier'),
    ('FUNCTION', 'm41_sett_subjektaktiv(text,uuid,boolean,text)',        'disponit_betaling_eier'),
    ('FUNCTION', 'm41_gjeldende_status(text,uuid,date)',                 'disponit_betaling_eier'),
    ('FUNCTION', 'm41_statushistorikken(text,uuid,integer)',             'disponit_betaling_eier'),
    ('FUNCTION', 'm41_abonnement_paa_dato(text,uuid,date)',              'disponit_betaling_eier'),
    ('FUNCTION', 'm41_betalingsstatus(text)',                            'disponit_betaling_eier'),
    ('FUNCTION', 'm41_subjektene(text,integer)',                         'disponit_betaling_eier'),
    ('FUNCTION', 'm41_tersklene(text)',                                  'disponit_betaling_eier'),
    ('FUNCTION', 'm41_funnkandidater(text,date)',                        'disponit_betaling_eier'),
    ('FUNCTION', 'm41_sveip_betalinger(integer)',                        'disponit_betaling_eier'),
    -- 112 (M-19): adresseregisterets doerer og adressesveipen.
    -- INGEN AV DEM SLAAR NOE OPP.
    ('FUNCTION', 'm19_evidens(text,uuid,text,text,jsonb)',              'disponit_adresse_eier'),
    ('FUNCTION', 'm19_normaliser(text)',                                'disponit_adresse_eier'),
    ('FUNCTION', 'm19_sett_krav(text,integer,integer,text[],text)',     'disponit_adresse_eier'),
    ('FUNCTION', 'm19_registrer_subjekt(text,uuid,text,text,text)',     'disponit_adresse_eier'),
    ('FUNCTION', 'm19_registrer_adresse(text,uuid,uuid,text,text,text,text,text,text,text,date,text,text)', 'disponit_adresse_eier'),
    ('FUNCTION', 'm19_registrer_kontroll(text,uuid,uuid,text,text,text,text,text,date,text)', 'disponit_adresse_eier'),
    ('FUNCTION', 'm19_sett_subjektaktiv(text,uuid,boolean,text)',        'disponit_adresse_eier'),
    ('FUNCTION', 'm19_gjeldende_adresse(text,uuid,date)',                'disponit_adresse_eier'),
    ('FUNCTION', 'm19_adressehistorikken(text,uuid,integer)',            'disponit_adresse_eier'),
    ('FUNCTION', 'm19_kontrollene(text,uuid,integer)',                   'disponit_adresse_eier'),
    ('FUNCTION', 'm19_adressestatus(text)',                              'disponit_adresse_eier'),
    ('FUNCTION', 'm19_subjektene(text,integer)',                         'disponit_adresse_eier'),
    ('FUNCTION', 'm19_kravene(text)',                                    'disponit_adresse_eier'),
    ('FUNCTION', 'm19_funnkandidater(text,date)',                        'disponit_adresse_eier'),
    ('FUNCTION', 'm19_sveip_adresser(integer)',                          'disponit_adresse_eier'),
    -- 113 (M-39): lønnsgrunnlagets doerer og lonnssveipen. INGEN AV
    -- DEM UTBETALER, og ingen av dem produserer en lonnsfil.
    ('FUNCTION', 'm39_evidens(text,uuid,text,text,jsonb)',              'disponit_lonn_eier'),
    ('FUNCTION', 'm39_sett_terskler(text,integer,integer,integer,integer,integer,text)', 'disponit_lonn_eier'),
    ('FUNCTION', 'm39_registrer_taker(text,uuid,text,text,text)',       'disponit_lonn_eier'),
    ('FUNCTION', 'm39_sett_arbeidsplan(text,uuid,uuid,integer,text,date,text,text)', 'disponit_lonn_eier'),
    ('FUNCTION', 'm39_registrer_timer(text,uuid,uuid,date,integer,text,text,text,text,text)', 'disponit_lonn_eier'),
    ('FUNCTION', 'm39_sett_takeraktiv(text,uuid,boolean,text)',         'disponit_lonn_eier'),
    ('FUNCTION', 'm39_plan_paa_dato(text,uuid,date)',                   'disponit_lonn_eier'),
    ('FUNCTION', 'm39_planene(text,uuid,integer)',                      'disponit_lonn_eier'),
    ('FUNCTION', 'm39_dagene(text,uuid,integer)',                       'disponit_lonn_eier'),
    ('FUNCTION', 'm39_timehistorikken(text,uuid,integer)',              'disponit_lonn_eier'),
    ('FUNCTION', 'm39_lonnsstatus(text)',                               'disponit_lonn_eier'),
    ('FUNCTION', 'm39_takerne(text,integer)',                           'disponit_lonn_eier'),
    ('FUNCTION', 'm39_tersklene(text)',                                 'disponit_lonn_eier'),
    ('FUNCTION', 'm39_funnkandidater(text,date)',                       'disponit_lonn_eier'),
    ('FUNCTION', 'm39_sveip_lonnsgrunnlag(integer)',                    'disponit_lonn_eier'),
    -- 114 (M-44): kampanjeregisterets doerer og kampanjesveipen.
    -- INGEN AV DEM SENDER NOE.
    ('FUNCTION', 'm44_evidens(text,uuid,text,text,jsonb)',              'disponit_kampanje_eier'),
    ('FUNCTION', 'm44_normaliser(text)',                                'disponit_kampanje_eier'),
    ('FUNCTION', 'm44_sett_grense(text,integer,integer,integer,text)',  'disponit_kampanje_eier'),
    ('FUNCTION', 'm44_registrer_mottaker(text,uuid,text,text,text,text)', 'disponit_kampanje_eier'),
    ('FUNCTION', 'm44_registrer_samtykke(text,uuid,uuid,text,text,text,text,date,text,text)', 'disponit_kampanje_eier'),
    ('FUNCTION', 'm44_registrer_kampanje(text,uuid,text,text,text,text,date,text)', 'disponit_kampanje_eier'),
    ('FUNCTION', 'm44_avlys_kampanje(text,uuid,text)',                  'disponit_kampanje_eier'),
    ('FUNCTION', 'm44_legg_i_plan(text,uuid,uuid,text)',                'disponit_kampanje_eier'),
    ('FUNCTION', 'm44_sett_mottakeraktiv(text,uuid,boolean,text)',      'disponit_kampanje_eier'),
    ('FUNCTION', 'm44_samtykke_paa_dato(text,uuid,date)',               'disponit_kampanje_eier'),
    ('FUNCTION', 'm44_samtykkehistorikken(text,uuid,integer)',          'disponit_kampanje_eier'),
    ('FUNCTION', 'm44_kampanjene(text,integer)',                        'disponit_kampanje_eier'),
    ('FUNCTION', 'm44_kampanjestatus(text)',                            'disponit_kampanje_eier'),
    ('FUNCTION', 'm44_mottakerne(text,integer)',                        'disponit_kampanje_eier'),
    ('FUNCTION', 'm44_grensene(text)',                                  'disponit_kampanje_eier'),
    ('FUNCTION', 'm44_funnkandidater(text,date)',                       'disponit_kampanje_eier'),
    ('FUNCTION', 'm44_sveip_kampanjer(integer)',                        'disponit_kampanje_eier'),
    -- 116 (M-48): motpartsregisterets doerer og
    -- motpartssveipen. KLYNGE 6s ENE UTGAAENDE KANAL:
    -- foretaksregisteret er koblet paa, kredittleverandoeren
    -- staar bak `modulen_hentet_kredittdata`. INGEN AV DEM
    -- SETTER EN KREDITTGRENSE ELLER AVSLAAR EN MOTPART.
    --
    -- `m48_oppslag_frosset()` staar IKKE her: radvakten lages
    -- etter `RESET ROLE` i seksjon 6 og eies av migratoren,
    -- som er det reparasjonen ellers ville satt den til.
    ('FUNCTION', 'm48_deaktiver_motpart(text,uuid,text)',                             'disponit_motpart_eier'),
    ('FUNCTION', 'm48_evidens(text,uuid,text,text,jsonb)',                            'disponit_motpart_eier'),
    ('FUNCTION', 'm48_fullfor_oppslag(text,uuid,text,text,text)',                     'disponit_motpart_eier'),
    ('FUNCTION', 'm48_funnene(text,boolean)',                                         'disponit_motpart_eier'),
    ('FUNCTION', 'm48_kravene(text)',                                                 'disponit_motpart_eier'),
    ('FUNCTION', 'm48_lukk_funn(text,uuid,text,text,text)',                           'disponit_motpart_eier'),
    ('FUNCTION', 'm48_motpartene(text,integer)',                                      'disponit_motpart_eier'),
    ('FUNCTION', 'm48_motpartsstatus(text)',                                          'disponit_motpart_eier'),
    ('FUNCTION', 'm48_oppslagene(text,uuid)',                                         'disponit_motpart_eier'),
    ('FUNCTION', 'm48_registrer_motpart(text,uuid,text,text,text)',                   'disponit_motpart_eier'),
    ('FUNCTION', 'm48_registrer_versjon(text,uuid,uuid,uuid,text,text,text,text,text,boolean,boolean,date,text)', 'disponit_motpart_eier'),
    ('FUNCTION', 'm48_registrer_vurdering(text,uuid,uuid,text,bigint,text,text)',     'disponit_motpart_eier'),
    ('FUNCTION', 'm48_registrert_vert()',                                             'disponit_motpart_eier'),
    ('FUNCTION', 'm48_reserver_oppslag(text,uuid,uuid,text,text,text,text)',          'disponit_motpart_eier'),
    ('FUNCTION', 'm48_sett_krav(text,integer,integer,integer,bigint,text[],text)',    'disponit_motpart_eier'),
    ('FUNCTION', 'm48_sveip_motparter(integer)',                                      'disponit_motpart_eier'),
    -- 117 (M-49): sanksjonskontrollens doerer og
    -- sanksjonssveipen. INGEN AV DEM BLOKKERER HANDEL, OG
    -- INGEN AVFEIER EN NAVNELIKHET. Beslutningen,
    -- motargumentet og utloeseren staar i toppen av
    -- migrasjon 117.
    ('FUNCTION', 'm49_avklar_treff(text,uuid,uuid,text,text,text)',                   'disponit_sanksjon_eier'),
    ('FUNCTION', 'm49_evidens(text,uuid,text,text,jsonb)',                            'disponit_sanksjon_eier'),
    ('FUNCTION', 'm49_funnene(text,boolean)',                                         'disponit_sanksjon_eier'),
    ('FUNCTION', 'm49_kontrollene(text,uuid)',                                        'disponit_sanksjon_eier'),
    ('FUNCTION', 'm49_kravene(text)',                                                 'disponit_sanksjon_eier'),
    ('FUNCTION', 'm49_listene(text)',                                                 'disponit_sanksjon_eier'),
    ('FUNCTION', 'm49_lukk_funn(text,uuid,text,text,text)',                           'disponit_sanksjon_eier'),
    ('FUNCTION', 'm49_normaliser(text)',                                              'disponit_sanksjon_eier'),
    ('FUNCTION', 'm49_registrer_kontroll(text,uuid,uuid,uuid,text[],jsonb,text)',     'disponit_sanksjon_eier'),
    ('FUNCTION', 'm49_registrer_liste(text,uuid,text,text,date,text,integer,text)',   'disponit_sanksjon_eier'),
    ('FUNCTION', 'm49_registrer_subjekt(text,uuid,text,text,text,text,date,text,text)', 'disponit_sanksjon_eier'),
    ('FUNCTION', 'm49_sanksjonsstatus(text)',                                         'disponit_sanksjon_eier'),
    ('FUNCTION', 'm49_sett_krav(text,integer,integer,integer,integer,text)',          'disponit_sanksjon_eier'),
    ('FUNCTION', 'm49_sett_subjektaktiv(text,uuid,boolean,text)',                     'disponit_sanksjon_eier'),
    ('FUNCTION', 'm49_subjektene(text,integer)',                                      'disponit_sanksjon_eier'),
    ('FUNCTION', 'm49_sveip_sanksjoner(integer)',                                     'disponit_sanksjon_eier'),
    -- 118 (M-46): anbudsregisterets doerer og anbudssveipen.
    -- INGEN AV DEM SENDER ET TILBUD, og ingen kan skrive et
    -- faktapunkt uten kilde: `utkastpunkt` har ingen
    -- fritekstkolonne.
    --
    -- `m46_utkast_frosset()` staar IKKE her: radvakten lages
    -- etter `RESET ROLE` i seksjon 6 og eies av migratoren.
    ('FUNCTION', 'm46_anbudene(text,integer)',                                        'disponit_anbud_eier'),
    ('FUNCTION', 'm46_anbudsstatus(text)',                                            'disponit_anbud_eier'),
    ('FUNCTION', 'm46_evidens(text,uuid,text,text,jsonb)',                            'disponit_anbud_eier'),
    ('FUNCTION', 'm46_funnene(text,boolean)',                                         'disponit_anbud_eier'),
    ('FUNCTION', 'm46_kildene(text)',                                                 'disponit_anbud_eier'),
    ('FUNCTION', 'm46_kravene(text,uuid,uuid)',                                       'disponit_anbud_eier'),
    ('FUNCTION', 'm46_lukk_funn(text,uuid,text,text,text)',                           'disponit_anbud_eier'),
    ('FUNCTION', 'm46_merk_klart(text,uuid,text)',                                    'disponit_anbud_eier'),
    ('FUNCTION', 'm46_opprett_utkast(text,uuid,uuid,text)',                           'disponit_anbud_eier'),
    ('FUNCTION', 'm46_profilen(text)',                                                'disponit_anbud_eier'),
    ('FUNCTION', 'm46_registrer_anbud(text,uuid,text,text,text,text,text,text,bigint,timestamp with time zone,text)', 'disponit_anbud_eier'),
    ('FUNCTION', 'm46_registrer_kilde(text,uuid,text,text,date,text,text)',           'disponit_anbud_eier'),
    ('FUNCTION', 'm46_registrer_krav(text,uuid,uuid,text,text,text,boolean,text)',    'disponit_anbud_eier'),
    ('FUNCTION', 'm46_registrer_punkt(text,uuid,uuid,uuid,uuid,text,text,text)',      'disponit_anbud_eier'),
    ('FUNCTION', 'm46_sett_anbudaktiv(text,uuid,boolean,text)',                       'disponit_anbud_eier'),
    ('FUNCTION', 'm46_sett_profil(text,text[],text[],bigint,bigint,integer,integer,text)', 'disponit_anbud_eier'),
    ('FUNCTION', 'm46_sveip_anbud(integer)',                                          'disponit_anbud_eier'),
    -- 119 (M-51): tilskuddsregisterets doerer og
    -- tilskuddssveipen. INGEN AV DEM SENDER EN SOEKNAD, og
    -- ingen kan sette et beloep uten kildepost:
    -- `tilskuddsestimat` har ingen beloepskolonne.
    --
    -- `m51_estimat_frosset()` staar IKKE her: radvakten
    -- lages etter `RESET ROLE` i seksjon 6 og eies av
    -- migratoren.
    ('FUNCTION', 'm51_estimatene(text,uuid)',                                         'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_evidens(text,uuid,text,text,jsonb)',                            'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_ferdigstill_estimat(text,uuid,text)',                           'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_forutsetningene(text,uuid)',                                    'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_funnene(text,boolean)',                                         'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_kildepostene(text,integer)',                                    'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_kravene(text)',                                                 'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_legg_til_forutsetning(text,uuid,uuid,text,text,text,text)',     'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_legg_til_post(text,uuid,uuid,uuid,bigint,text,text)',           'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_lukk_funn(text,uuid,text,text,text)',                           'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_opprett_estimat(text,uuid,uuid,date,date,text)',                'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_ordningene(text,integer)',                                      'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_postene(text,uuid)',                                            'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_registrer_kildepost(text,uuid,text,text,text,bigint,date,date,text)', 'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_registrer_ordning(text,uuid,text,text,text,text,text,bigint,integer,timestamp with time zone,text)', 'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_sett_krav(text,integer,integer,integer,text,text)',                  'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_sett_ordningaktiv(text,uuid,boolean,text)',                     'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_sveip_tilskudd(integer)',                                       'disponit_tilskudd_eier'),
    ('FUNCTION', 'm51_tilskuddsstatus(text)',                                         'disponit_tilskudd_eier'),
    -- 120 (M-55): merkevareregisterets doerer og merkevaresveipen.
    -- INGEN AV DEM SENDER ET KRAV ELLER EN KLAGE, og ingen kan
    -- registrere et funn uten bevaringskopi: `merkevarefunn.kopi_id`
    -- er NOT NULL med fremmednoekkel.
    --
    -- `m55_funn_frosset()` staar IKKE her: radvakten lages etter
    -- `RESET ROLE` i seksjon 6 og eies av migratoren.
    ('FUNCTION', 'm55_algoritmeversjon()',                                            'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_bevaringskopiene(text,integer)',                                'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_evidens(text,uuid,text,text,jsonb)',                            'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_funnene(text,uuid,integer)',                                    'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_grunnlag(text,text,boolean)',                                   'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_henvis_funn(text,uuid,uuid,text)',                              'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_kravene(text)',                                                 'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_likhet(text,text)',                                             'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_lukk_funn(text,uuid,text,text)',                                'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_lukk_varsel(text,uuid,text,text)',                              'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_merkene(text,integer)',                                         'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_merkevarestatus(text)',                                         'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_normaliser(text)',                                              'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_registrer_bevaringskopi(text,uuid,text,timestamp with time zone,text,bigint,text,text,text)', 'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_registrer_funn(text,uuid,uuid,uuid,text,text,text,text,text)',  'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_registrer_merkevare(text,uuid,text,text,text,text,text[],date,text)', 'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_sett_krav(text,integer,integer,integer,text,text)',             'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_sett_merkevare_aktiv(text,uuid,boolean,text)',                  'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_sveip_merkevare(integer)',                                      'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_varslene(text,boolean)',                                        'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_vurder_funn(text,uuid,uuid,text)',                              'disponit_merkevare_eier'),
    ('FUNCTION', 'm55_vurderingene(text,uuid)',                                       'disponit_merkevare_eier'),
    -- 121 (M-54): EHF-registerets doerer og EHF-sveipen. INGEN AV
    -- DEM SENDER EN FAKTURA, og ingen dommer mot et utloept regelsett.
    --
    -- `m54_regelsett_frosset()` og `m54_retting_frosset()` staar IKKE
    -- her: radvaktene lages etter `RESET ROLE` i seksjon 6 og eies av
    -- migratoren.
    ('FUNCTION', 'm54_avvikene(text,uuid)',                                           'disponit_ehf_eier'),
    ('FUNCTION', 'm54_dokumentene(text,integer)',                                     'disponit_ehf_eier'),
    ('FUNCTION', 'm54_ehfstatus(text)',                                               'disponit_ehf_eier'),
    ('FUNCTION', 'm54_evidens(text,uuid,text,text,jsonb)',                            'disponit_ehf_eier'),
    ('FUNCTION', 'm54_funnene(text,boolean)',                                         'disponit_ehf_eier'),
    ('FUNCTION', 'm54_kravene(text)',                                                 'disponit_ehf_eier'),
    ('FUNCTION', 'm54_lukk_funn(text,uuid,text,text)',                                'disponit_ehf_eier'),
    ('FUNCTION', 'm54_merk_klar(text,uuid,text)',                                     'disponit_ehf_eier'),
    ('FUNCTION', 'm54_regelsett_gyldig(date,date)',                                   'disponit_ehf_eier'),
    ('FUNCTION', 'm54_regelsettene(text,integer)',                                    'disponit_ehf_eier'),
    ('FUNCTION', 'm54_reglene(text,uuid)',                                            'disponit_ehf_eier'),
    ('FUNCTION', 'm54_registrer_dokument(text,uuid,text,text,text,date,text,bigint,text,text)', 'disponit_ehf_eier'),
    ('FUNCTION', 'm54_registrer_felter(text,uuid,text[],integer[],text[],bigint[],text)', 'disponit_ehf_eier'),
    ('FUNCTION', 'm54_registrer_regel(text,uuid,uuid,text,text,text,text[],text,text,text,text)', 'disponit_ehf_eier'),
    ('FUNCTION', 'm54_registrer_regelsett(text,uuid,text,text,date,date,text,text,text)', 'disponit_ehf_eier'),
    ('FUNCTION', 'm54_registrer_retting(text,uuid,uuid,text,text,text,text,text)',     'disponit_ehf_eier'),
    ('FUNCTION', 'm54_sett_gyldig_til(text,uuid,date,text)',                          'disponit_ehf_eier'),
    ('FUNCTION', 'm54_sett_krav(text,integer,integer,text,text)',                      'disponit_ehf_eier'),
    ('FUNCTION', 'm54_sveip_ehf(integer)',                                            'disponit_ehf_eier'),
    ('FUNCTION', 'm54_valider_dokument(text,uuid,uuid,uuid,text)',                    'disponit_ehf_eier'),
    ('FUNCTION', 'm54_valideringene(text,uuid)',                                      'disponit_ehf_eier'),
    -- 124 (M-50): journalregisterets doerer og postjournalsveipen.
    -- INGEN AV DEM HENTER. Postjournaler ER offentlige, saa den
    -- vanlige innvendingen treffer ikke. Det som treffer er at ti
    -- tusen oppslag sammenstilt i et register er en PROFIL, og
    -- profilen er vaar — ikke kommunens.
    --
    -- `m50_anonymiser` ER MODULENS RYDDEDOER, og den sletter ikke:
    -- at vi HAR oppbevart noen skal fortsatt kunne leses, uten navnet.
    --
    -- `m50_kilde_frosset()` og `m50_person_frosset()` staar IKKE her:
    -- radvaktene lages etter `RESET ROLE` og eies av migratoren.
    ('FUNCTION', 'm50_anonymiser(text,uuid,text)',                                   'disponit_postjournal_eier'),
    ('FUNCTION', 'm50_bildet(text,integer)',                                         'disponit_postjournal_eier'),
    ('FUNCTION', 'm50_evidens(text,uuid,text,text,jsonb)',                           'disponit_postjournal_eier'),
    ('FUNCTION', 'm50_funn_er_sveipens(text)',                                       'disponit_postjournal_eier'),
    ('FUNCTION', 'm50_funnene(text,boolean)',                                        'disponit_postjournal_eier'),
    ('FUNCTION', 'm50_kilde_gyldig(date,date)',                                      'disponit_postjournal_eier'),
    ('FUNCTION', 'm50_kildene(text,integer)',                                        'disponit_postjournal_eier'),
    ('FUNCTION', 'm50_lukk_funn(text,uuid,text,text)',                               'disponit_postjournal_eier'),
    ('FUNCTION', 'm50_opprett_sak(text,uuid,text,text,text,text)',                   'disponit_postjournal_eier'),
    ('FUNCTION', 'm50_personene(text,uuid)',                                         'disponit_postjournal_eier'),
    ('FUNCTION', 'm50_postene(text,integer)',                                        'disponit_postjournal_eier'),
    ('FUNCTION', 'm50_registrer_kilde(text,uuid,text,text,text,text,date,date,text,text,text)', 'disponit_postjournal_eier'),
    ('FUNCTION', 'm50_registrer_post(text,uuid,uuid,uuid,text,date,text,text,text,date,text[],text[],date[],text)', 'disponit_postjournal_eier'),
    ('FUNCTION', 'm50_sakene(text,integer)',                                         'disponit_postjournal_eier'),
    ('FUNCTION', 'm50_sett_gyldig_til(text,uuid,date,text)',                         'disponit_postjournal_eier'),
    ('FUNCTION', 'm50_sett_krav(text,integer,integer,integer,text,text)',            'disponit_postjournal_eier'),
    ('FUNCTION', 'm50_sveip_postjournal(integer)',                                   'disponit_postjournal_eier'),
    -- 123 (M-47): pliktregisterets doerer og myndighetssveipen.
    -- INGEN AV DEM SENDER INN. En innsending til en myndighet er
    -- BINDENDE og kan ikke kalles tilbake — `m47_registrer_bevis`
    -- registrerer at et MENNESKE har sendt inn, et annet sted.
    --
    -- MEN HER ER FRAVAERET IKKE NOK: en frist som gaar uten
    -- innsending er nettopp skaden. Derfor eier sveipen to funn ingen
    -- kan lukke for haand (`m47_funn_er_sveipens`).
    --
    -- `m47_regelverk_frosset()` staar IKKE her: radvakten lages etter
    -- `RESET ROLE` og eies av migratoren, som i 122.
    ('FUNCTION', 'm47_bildet(text,integer)',                                         'disponit_myndighet_eier'),
    ('FUNCTION', 'm47_evidens(text,uuid,text,text,jsonb)',                           'disponit_myndighet_eier'),
    ('FUNCTION', 'm47_funn_er_sveipens(text)',                                       'disponit_myndighet_eier'),
    ('FUNCTION', 'm47_funnene(text,boolean)',                                        'disponit_myndighet_eier'),
    ('FUNCTION', 'm47_lukk_funn(text,uuid,text,text)',                               'disponit_myndighet_eier'),
    ('FUNCTION', 'm47_pliktene(text,integer)',                                       'disponit_myndighet_eier'),
    ('FUNCTION', 'm47_plikttypene(text,integer)',                                    'disponit_myndighet_eier'),
    ('FUNCTION', 'm47_regelverk_gyldig(date,date)',                                  'disponit_myndighet_eier'),
    ('FUNCTION', 'm47_regelverkene(text,integer)',                                   'disponit_myndighet_eier'),
    ('FUNCTION', 'm47_registrer_bevis(text,uuid,uuid,date,text,text,text,text)',     'disponit_myndighet_eier'),
    ('FUNCTION', 'm47_registrer_plikt(text,uuid,uuid,uuid,date,date,date,text)',     'disponit_myndighet_eier'),
    ('FUNCTION', 'm47_registrer_plikttype(text,uuid,text,text,text,text,text)',      'disponit_myndighet_eier'),
    ('FUNCTION', 'm47_registrer_regelverk(text,uuid,text,text,text,text,date,date,text,text,text)', 'disponit_myndighet_eier'),
    ('FUNCTION', 'm47_sett_gyldig_til(text,uuid,date,text)',                         'disponit_myndighet_eier'),
    ('FUNCTION', 'm47_sett_krav(text,integer,integer,integer,text,text)',            'disponit_myndighet_eier'),
    ('FUNCTION', 'm47_sveip_myndighetsplikt(integer)',                               'disponit_myndighet_eier'),
    -- 122 (M-52): tollkoderegisterets doerer og tollkodesveipen.
    -- INGEN AV DEM DEKLARERER, og ingen avgir et forslag uten
    -- grunnlag: `m52_avgi_forslag` skriver forslaget og grunnene i
    -- SAMME setning.
    --
    -- `m52_nomenklatur_frosset()` og `m52_forslag_frosset()` staar
    -- IKKE her: radvaktene lages etter `RESET ROLE` i seksjon 6 og
    -- eies av migratoren.
    ('FUNCTION', 'm52_avgi_forslag(text,uuid,uuid,uuid,integer,text[],text[],text[],date[],text)', 'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_evidens(text,uuid,text,text,jsonb)',                            'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_forslagene(text,uuid)',                                         'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_funnene(text,boolean)',                                         'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_grunnene(text,uuid)',                                           'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_kravene(text)',                                                  'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_lukk_funn(text,uuid,text,text)',                                'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_merk_klart(text,uuid,text)',                                    'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_nomenklatur_gyldig(date,date)',                                 'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_nomenklaturene(text,integer)',                                  'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_registrer_nomenklatur(text,uuid,text,text,date,date,text,text,text)', 'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_registrer_vare(text,uuid,text,text,text,text,text,text)',       'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_registrer_varenummer(text,uuid,uuid,text,text,integer,text)',   'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_sett_gyldig_til(text,uuid,date,text)',                          'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_sett_krav(text,integer,integer,integer,text,text)',             'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_sveip_tollkode(integer)',                                       'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_tollstatus(text)',                                              'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_varene(text,integer)',                                          'disponit_tollkode_eier'),
    ('FUNCTION', 'm52_varenumrene(text,uuid,integer)',                                'disponit_tollkode_eier'),
    ('FUNCTION', 'm46_utkastene(text,uuid)',                                          'disponit_anbud_eier'),
    ('FUNCTION', 'm49_treffene(text,uuid)',                                           'disponit_sanksjon_eier'),
    ('FUNCTION', 'm48_versjonene(text,uuid)',                                         'disponit_motpart_eier'),
    ('FUNCTION', 'm48_vurderingene(text,uuid)',                                       'disponit_motpart_eier'),
    -- 115: sveipestatusens doerer. Plattformskopet (090s form), eid av
    -- samme rolle som resten av plattformdoerene.
    ('FUNCTION', 'registrer_sveipestatus(text,timestamp with time zone,integer,integer,boolean,boolean,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'sveipeflaaten(text)',                                 'disponit_m37_claimer'),
    ('FUNCTION', 'sveipeobservasjonen(text)',                           'disponit_m37_claimer'),
    ('FUNCTION', 'varsle_sveip_uteblitt(text)',                         'disponit_m37_claimer'),
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
    ('FUNCTION', 'utsted_challenge_selvbetjent(text,text,boolean,text,text)', 'disponit_domene_eier'),
    -- 093 (M-4): retensjonsregisteret. De fem tabellene og de tolv
    -- funksjonene eies av NOLOGIN-rollen disponit_lager_eier, og
    -- eierskapet ER hele sikkerhetsargumentet: runtime naar tabellene
    -- KUN gjennom de fire lesedoerene, maalerollen KUN gjennom
    -- m4_mal_lagre, og kolonnegrantene paa de maalte lagrene er gitt til
    -- NETTOPP denne rollen. Flyttes eierskapet til migrator, leser
    -- definer-veiene som migrator -- som ikke har m4_maaler-policyen paa
    -- de maalte tabellene og derfor faar null rader tilbake i stedet for
    -- en feil. En stille null er nøyaktig det modulen finnes for aa ikke
    -- levere. INGEN semikolon i denne kommentaren -- VALUES-blokken
    -- parses paa setningsskilletegnet
    ('TABLE',    'retensjonslager',      'disponit_lager_eier'),
    ('TABLE',    'retensjonsmaaling',    'disponit_lager_eier'),
    ('TABLE',    'retensjonsstorrelse',  'disponit_lager_eier'),
    ('TABLE',    'retensjonsbeholdning', 'disponit_lager_eier'),
    ('TABLE',    'retensjonsfunn',       'disponit_lager_eier'),
    -- Registervaktene. De slaar opp i information_schema/pg_proc paa
    -- vegne av den som skriver registeret, og maa ha eierens identitet
    ('FUNCTION', 'm4_lager_finnes_i_basen()',   'disponit_lager_eier'),
    ('FUNCTION', 'm4_reaper_finnes_i_basen()',  'disponit_lager_eier'),
    -- Append-only-vaktene paa de tre maalelagrene
    ('FUNCTION', 'm4_maaling_vakt()',           'disponit_lager_eier'),
    ('FUNCTION', 'm4_aggregat_vakt()',          'disponit_lager_eier'),
    -- Maaleveien og modulens ene reaper
    ('FUNCTION', 'm4_registrer_funn(uuid,text,text,text,text,jsonb)', 'disponit_lager_eier'),
    ('FUNCTION', 'm4_registerkolonner()',       'disponit_lager_eier'),
    ('FUNCTION', 'm4_reap_egne_maalinger(integer)', 'disponit_lager_eier'),
    ('FUNCTION', 'm4_mal_lagre(integer,boolean)',   'disponit_lager_eier'),
    -- De fire lesedoerene -- de gaar gjennom krev_tenantkontekst-porten
    ('FUNCTION', 'm4_siste_maaling(text)',      'disponit_lager_eier'),
    ('FUNCTION', 'm4_retensjonsbilde(text)',    'disponit_lager_eier'),
    ('FUNCTION', 'm4_retensjonskatalog(text)',  'disponit_lager_eier'),
    ('FUNCTION', 'm4_retensjonsfunn(text)',     'disponit_lager_eier'),
    -- 127 (M-53): avviksregisterets doerer og HMS-sveipen.
    --
    -- ANONYMT AVVIK ER EN TILSTAND OG IKKE ET TOMT NAVNEFELT. Doeren
    -- `m53_meld_avvik` nekter et anonymt avvik som baerer et navn eller
    -- en aktoer, og skriver avviket og melderen i SAMME setning.
    --
    -- `m53_anonymiser` ER MODULENS RYDDEDOER, og den sletter ikke: at
    -- vi HAR hatt avviket er nettopp det Arbeidstilsynet etterproever.
    --
    -- De fem radvaktene (`m53_avvik_frosset()`, `m53_regel_frosset()`,
    -- `m53_melder_frosset()`, `m53_melder_krever_navngitt()` og
    -- `m53_tiltak_append_only()`) staar IKKE her: de eies av migrator,
    -- som eier tabellene triggerne henger paa.
    --
    -- `m53_funn_er_sveipens` og `m53_regel_gyldig` staar her OG lages av
    -- modulrollen i 127. I 124 gjoer de det ikke, og reparasjonen
    -- flytter dem hver gang den kjoerer.
    ('FUNCTION', 'm53_anonymiser(text,uuid,text,text)',                                         'disponit_hms_eier'),
    ('FUNCTION', 'm53_avvikene(text,integer)',                                                  'disponit_hms_eier'),
    ('FUNCTION', 'm53_bildet(text)',                                                            'disponit_hms_eier'),
    ('FUNCTION', 'm53_evidens(text,uuid,text,text,jsonb)',                                      'disponit_hms_eier'),
    ('FUNCTION', 'm53_funn_er_sveipens(text)',                                                  'disponit_hms_eier'),
    ('FUNCTION', 'm53_funnene(text,boolean)',                                                   'disponit_hms_eier'),
    ('FUNCTION', 'm53_krev_samme_avvik(text,uuid,text,text,text,text,date)',       'disponit_hms_eier'),
    ('FUNCTION', 'm53_lukk_funn(text,uuid,text,text)',                                          'disponit_hms_eier'),
    ('FUNCTION', 'm53_meld_avvik(text,uuid,text,text,text,text,date,text,text,text)',           'disponit_hms_eier'),
    ('FUNCTION', 'm53_oppbevaringsgrunnlag(text,uuid)',                                         'disponit_hms_eier'),
    ('FUNCTION', 'm53_regel_gyldig(date,date)',                                                 'disponit_hms_eier'),
    ('FUNCTION', 'm53_regelverket(text)',                                                       'disponit_hms_eier'),
    ('FUNCTION', 'm53_registrer_regel(text,uuid,text,text,text,integer,boolean,date,date,text)', 'disponit_hms_eier'),
    ('FUNCTION', 'm53_registrer_tiltak(text,uuid,uuid,text,boolean,date,text)',                 'disponit_hms_eier'),
    ('FUNCTION', 'm53_sett_krav(text,integer,integer,integer,integer,text,text)',               'disponit_hms_eier'),
    ('FUNCTION', 'm53_sveip_hms(integer)',                                                      'disponit_hms_eier'),
    ('FUNCTION', 'm53_tiltakene(text,uuid)',                                                    'disponit_hms_eier');

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
