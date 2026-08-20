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
    ('FUNCTION', 'sikre_sak_for_oppdrag(text,bigint,text,text,text)', 'disponit_m37_claimer'),
    -- 043 (Gate 14b): oppløsningsveien — kansellering med fencing.
    ('FUNCTION', 'bruk_kvitteringskapabilitet(text,text,text)',        'disponit_m37_claimer'),
    ('FUNCTION', 'avvis_med_opplosning(text,bigint,bigint[],text,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'reversibilitet_for_oppdrag(text,bigint)',            'disponit_m37_claimer'),
    ('FUNCTION', 'reap_evidensfrister(integer)',                      'disponit_m37_claimer'),
    -- 038 §4-porten (Codex P1): binder `p_tenant` til kallerens
    -- tenantkontekst i definer-veiene over. Opprettes i det samme
    -- SET ROLE-vinduet og hoerer derfor til den samme eieren.
    ('FUNCTION', 'krev_tenantkontekst(text,text)',                    'disponit_m37_claimer'),
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
    ('FUNCTION', 'attester_ci_kjoring(text,text,text,text,text,text,text)', 'disponit_modul_eier'),
    ('FUNCTION', 'attester_evidensfil(text,text,text,jsonb,text)',    'disponit_modul_eier'),
    ('FUNCTION', 'maal_rent_utfall(text,bigint)',                     'disponit_modul_eier'),
    ('FUNCTION', 'bytt_release(text,text,text,integer,text,text)',    'disponit_modul_eier'),
    ('FUNCTION', 'pensjoner_release(text,text,text,text)',            'disponit_modul_eier'),
    ('FUNCTION', 'noddeaktiver_modul(text,text,text)',               'disponit_modul_eier'),
    ('FUNCTION', 'reaktiver_modul(text,bigint,text)',                'disponit_modul_eier'),
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
